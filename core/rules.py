"""后台规则引擎：多图标规则，出现/消失时执行脚本动作。

单执行流：引擎只负责发动作，真正的播放控制由主窗口的 PlaybackState 执行。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .capture import make_source
from .log import log
from .screen import TemplateMatcher


def _action_list(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [dict(a) for a in value if isinstance(a, dict)]


def normalize_rule(rule: dict) -> dict:
    r = dict(rule or {})
    r.setdefault("id", "rule_%d" % int(time.time() * 1000))
    r.setdefault("name", "规则")
    r.setdefault("enabled", True)
    r.setdefault("priority", 50)
    r.setdefault("template", "")
    r.setdefault("threshold", 0.85)
    r.setdefault("region", [0, 0, 0, 0])
    r.setdefault("capture_mode", "screen")
    r.setdefault("window_title", "")
    r.setdefault("window_class", "")
    r.setdefault("window_process", "")
    r.setdefault("window_hwnd", 0)
    r.setdefault("poll_ms", 150)
    r.setdefault("stable_frames", 2)
    r.setdefault("cooldown_ms", 500)
    r.setdefault("multiscale", True)
    r["on_hit"] = _action_list(r.get("on_hit"))
    r["on_miss"] = _action_list(r.get("on_miss"))
    return r


@dataclass
class RuleState:
    present: bool = False
    hit_streak: int = 0
    miss_streak: int = 0
    last_fire: float = 0.0
    last_hit: bool = False
    score: float = 0.0
    error: str = ""
    next_due: float = 0.0


class BackgroundRuleEngine(threading.Thread):
    def __init__(self, config_provider: Callable[[], dict],
                 on_action: Callable[[str, dict], None]):
        super().__init__(name="wizflow-rules", daemon=True)
        self.config_provider = config_provider
        self.on_action = on_action
        self.matcher = TemplateMatcher()
        self._stop = threading.Event()
        self._states: dict[str, RuleState] = {}
        self._sources: dict[tuple, object] = {}
        self._group_had_any = False
        self._group_none_since = 0.0
        self.enabled = False

    def stop(self) -> None:
        self._stop.set()

    def _close_sources(self):
        for src in list(self._sources.values()):
            try:
                src.close()
            except Exception:
                pass
        self._sources.clear()

    def _source(self, rule: dict):
        key = (rule.get("capture_mode", "screen"), int(rule.get("window_hwnd", 0) or 0),
               rule.get("window_title", ""), rule.get("window_class", ""),
               rule.get("window_process", ""))
        if key not in self._sources:
            self._sources[key] = make_source(rule)
        return self._sources[key]

    def _state(self, rid: str) -> RuleState:
        st = self._states.get(rid)
        if st is None:
            st = RuleState()
            self._states[rid] = st
        return st

    def _fire(self, rule: dict, actions):
        for act in actions or []:
            try:
                self.on_action(str(rule.get("id", "")), dict(act))
            except Exception as exc:
                log("后台规则动作派发失败: %s" % exc, "WARN")

    def run(self) -> None:
        while not self._stop.is_set():
            cfg = self.config_provider() or {}
            raw = cfg.get("background_rules") or []
            rules = [normalize_rule(r) for r in raw if isinstance(r, dict)]
            self.enabled = any(bool(r.get("enabled", True)) for r in rules)
            if not rules:
                self._close_sources()
                self._states.clear()
                self._stop.wait(0.5)
                continue
            now = time.time()
            evaluated = []
            for rule in rules:
                rid = str(rule.get("id") or "")
                st = self._state(rid)
                if not bool(rule.get("enabled", True)):
                    st.present = False
                    st.error = ""
                    continue
                if now < st.next_due:
                    evaluated.append((rule, st))
                    continue
                try:
                    region = [int(v) for v in (rule.get("region") or [0, 0, 0, 0])]
                    if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
                        raise ValueError("监控区域未设置")
                    template = str(rule.get("template", "") or "")
                    if not template:
                        raise ValueError("未选择监控图案")
                    source = self._source(rule)
                    frame = source.grab(region)
                    res = self.matcher.find(frame, template,
                                            float(rule.get("threshold", 0.85)),
                                            bool(rule.get("multiscale", True)))
                    hit = bool(res.score >= float(rule.get("threshold", 0.85)))
                    st.last_hit = hit
                    st.score = float(res.score)
                    st.error = ""
                except Exception as exc:
                    st.error = str(exc)
                    st.last_hit = False
                    st.score = 0.0
                    st.next_due = now + max(0.3, int(rule.get("poll_ms", 150)) / 1000.0)
                    evaluated.append((rule, st))
                    continue
                st.next_due = now + max(0.05, int(rule.get("poll_ms", 150)) / 1000.0)
                evaluated.append((rule, st))

            # 1) 稳定帧 + 触发
            for rule, st in evaluated:
                stable = max(1, int(rule.get("stable_frames", 2)))
                if st.last_hit:
                    st.hit_streak += 1
                    st.miss_streak = 0
                else:
                    st.miss_streak += 1
                    st.hit_streak = 0

            group = cfg.get("background_group") or {}
            mode = str(group.get("mode", "parallel"))

            fire_on = []
            fire_off = []
            for rule, st in evaluated:
                if not bool(rule.get("enabled", True)):
                    continue
                stable = max(1, int(rule.get("stable_frames", 2)))
                if not st.present and st.hit_streak >= stable:
                    st.present = True
                    fire_on.append((rule, st))
                elif st.present and st.miss_streak >= stable:
                    st.present = False
                    fire_off.append((rule, st))

            present_priorities = {}
            for rule, st in evaluated:
                if st.present and bool(rule.get("enabled", True)):
                    present_priorities[str(rule.get("id"))] = int(rule.get("priority", 50))

            for rule, st in fire_on:
                rid = str(rule.get("id", ""))
                cooldown = max(0, int(rule.get("cooldown_ms", 500))) / 1000.0
                if now - st.last_fire < cooldown:
                    continue
                if mode == "mutex":
                    prio = int(rule.get("priority", 50))
                    others = [p for k, p in present_priorities.items() if k != rid]
                    if others and max(others) > prio:
                        continue
                st.last_fire = now
                self._fire(rule, rule.get("on_hit"))
            for rule, st in fire_off:
                cooldown = max(0, int(rule.get("cooldown_ms", 500))) / 1000.0
                if now - st.last_fire < cooldown:
                    continue
                st.last_fire = now
                self._fire(rule, rule.get("on_miss"))

            # 2) 组级：所有规则都不存在
            on_none = _action_list(group.get("on_none_present"))
            if on_none:
                any_present = any(st.present for _, st in evaluated)
                grace = max(0, int(group.get("grace_ms", 600))) / 1000.0
                if any_present:
                    self._group_had_any = True
                    self._group_none_since = 0.0
                elif self._group_had_any:
                    if not self._group_none_since:
                        self._group_none_since = now
                    elif now - self._group_none_since >= grace:
                        self._group_had_any = False
                        self._group_none_since = 0.0
                        for act in on_none:
                            try:
                                self.on_action("", dict(act))
                            except Exception as exc:
                                log("组动作派发失败: %s" % exc, "WARN")

            # 清理已删除规则的 source/state
            ids = {str(r.get("id")) for r in rules}
            for rid in list(self._states):
                if rid not in ids:
                    self._states.pop(rid, None)

            self._stop.wait(0.05)
        self._close_sources()

    def status(self) -> list[dict]:
        out = []
        for rid, st in list(self._states.items()):
            out.append({"id": rid, "present": st.present, "score": st.score,
                        "error": st.error})
        return out
