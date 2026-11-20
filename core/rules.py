"""后台常驻规则引擎（场景 → 规则 → 组）。

读取 multi_monitor：
  region/capture  = 场景
  rules[].resident= 常驻规则
  links           = 组
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .capture import make_source
from .log import log
from .monitor_rules import match_rule, normalize_monitor_group
from .screen import TemplateMatcher


@dataclass
class RuleState:
    present: bool = False
    hit_streak: int = 0
    miss_streak: int = 0
    hit_since: float = 0.0
    miss_since: float = 0.0
    presence_fires: int = 0
    created_at: float = field(default_factory=time.time)
    last_fire: float = 0.0
    score: float = 0.0
    error: str = ""
    center: tuple = (0, 0)
    last_res: object = None
    cost_ms: float = 0.0
    next_due: float = 0.0
    count: int = 0


class BackgroundRuleEngine(threading.Thread):
    def __init__(self, config_provider: Callable[[], dict],
                 on_action: Callable[[str, dict], None]):
        super().__init__(name="wizflow-rules", daemon=True)
        self.config_provider = config_provider
        self.on_action = on_action
        self.matcher = TemplateMatcher()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._states: dict[str, RuleState] = {}
        self._source = None
        self._source_key = None
        self._group_had_any = False
        self._group_none_since = 0.0
        self._link_best = {}
        self._link_fired = {}
        self._link_step = {}
        self.enabled = False
        self.grab_ms = 0.0

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def stop(self) -> None:
        self._stop.set()

    def _close_source(self):
        src = self._source
        self._source = None
        self._source_key = None
        if src is not None:
            try:
                src.close()
            except Exception:
                pass

    def _ensure_source(self, group: dict):
        key = (group.get("capture_mode", "screen"), int(group.get("window_hwnd", 0) or 0),
               group.get("window_title", ""), group.get("window_class", ""),
               group.get("window_process", ""))
        if self._source is not None and self._source_key == key:
            return self._source
        self._close_source()
        self._source = make_source(group)
        self._source_key = key
        return self._source

    def _state(self, rid: str) -> RuleState:
        st = self._states.get(rid)
        if st is None:
            st = RuleState()
            self._states[rid] = st
        return st

    def _fire(self, rule: dict, actions, center):
        for act in actions or []:
            if not isinstance(act, dict):
                continue
            item = dict(act)
            item["_center"] = list(center or (0, 0))
            try:
                self.on_action(str(rule.get("id", "")), item)
            except Exception as exc:
                log("后台规则动作派发失败: %s" % exc, "WARN")

    def run(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                self._close_source()
                self._stop.wait(0.2)
                continue
            cfg = self.config_provider() or {}
            group = normalize_monitor_group(cfg.get("multi_monitor") or {})
            region = [int(v) for v in (group.get("region") or [0, 0, 0, 0])]
            all_ids = {str(r.get("id")) for r in group.get("rules", [])}
            for rid in list(self._states):
                if rid not in all_ids:
                    self._states.pop(rid, None)
            rules = [r for r in group.get("rules", [])
                     if bool(r.get("enabled", True)) and bool(r.get("resident", False))]
            self.enabled = bool(rules)
            if not rules or len(region) != 4 or region[2] <= 0 or region[3] <= 0:
                self._close_source()
                self._stop.wait(0.4)
                continue
            now = time.time()
            due = []
            for rule in rules:
                st = self._state(str(rule.get("id")))
                if now >= st.next_due:
                    due.append(rule)
            if not due:
                nxt = min(self._state(str(r.get("id"))).next_due for r in rules)
                self._stop.wait(max(0.02, min(0.2, nxt - now)))
                continue
            try:
                source = self._ensure_source(group)
                t_grab = time.perf_counter()
                frame = source.grab(region)
                self.grab_ms = (time.perf_counter() - t_grab) * 1000.0
            except Exception as exc:
                for rule in rules:
                    st = self._state(str(rule.get("id")))
                    st.error = str(exc)
                self._close_source()
                self._stop.wait(0.5)
                continue

            now = time.time()
            origin = (0, 0) if getattr(source, "mode", "screen") == "screen" else source.origin
            results = {}
            for rule in rules:
                rid = str(rule.get("id"))
                st = self._state(rid)
                poll = int(rule.get("poll_ms", 0) or group.get("poll_ms", 150) or 150)
                if now < st.next_due:
                    results[rid] = (rule, st, st.last_hit, st.last_res, False)
                    continue
                try:
                    if not rule.get("templates") and not rule.get("template"):
                        raise ValueError("未选择图标模板")
                    t_match = time.perf_counter()
                    res = match_rule(self.matcher, frame, region, rule, origin)
                    st.cost_ms = (time.perf_counter() - t_match) * 1000.0
                    hit = res.score >= float(rule.get("threshold", 0.85))
                    st.score = float(res.score)
                    st.error = ""
                    st.center = tuple(res.center)
                    st.last_hit = hit
                    st.last_res = res
                    st.next_due = now + max(0.03, poll / 1000.0)
                    results[rid] = (rule, st, hit, res, True)
                except Exception as exc:
                    st.error = str(exc)
                    st.hit_streak = 0
                    st.miss_streak = 0
                    st.next_due = now + 0.5
                    results[rid] = (rule, st, False, None, True)

            # 持续时间 / 消失时间计时
            for rid, (rule, st, hit, res, evaluated) in results.items():
                if not evaluated:
                    continue
                if hit:
                    if st.hit_since == 0:
                        st.hit_since = now
                    st.miss_since = 0
                    st.hit_streak += 1
                    st.miss_streak = 0
                else:
                    if st.miss_since == 0:
                        st.miss_since = now
                    st.hit_since = 0
                    st.miss_streak += 1
                    st.hit_streak = 0

            links = [l for l in (group.get("links") or []) if isinstance(l, dict)]
            member_link = {}
            for link in links:
                for rid in link.get("members", []):
                    member_link[str(rid)] = link

            fire_on = []
            fire_off = []
            for rid, (rule, st, hit, res, evaluated) in results.items():
                if not evaluated:
                    continue
                present_ms = max(0, int(rule.get("present_ms", 0) or 0))
                absent_ms = max(0, int(rule.get("absent_ms", 600) or 0))
                if (not st.present and hit and st.hit_since
                        and (now - st.hit_since) * 1000.0 >= present_ms):
                    st.present = True
                    st.presence_fires = 0
                    if rid not in member_link:
                        fire_on.append((rule, st))
                elif (st.present and not hit and st.miss_since
                      and (now - st.miss_since) * 1000.0 >= absent_ms):
                    st.present = False
                    st.presence_fires = 0
                    fire_off.append((rule, st))

            def can_fire(rule, st):
                cooldown = max(0, int(rule.get("cooldown_ms", 0))) / 1000.0
                if now - st.last_fire < cooldown:
                    return False
                fires = max(0, int(rule.get("fires_per_present", 1) or 0))
                if fires and st.presence_fires >= fires:
                    return False
                return True

            def fire_rule(rule, st):
                if not can_fire(rule, st):
                    return
                st.last_fire = now
                st.count += 1
                st.presence_fires += 1
                self._fire(rule, rule.get("actions"), st.center)

            present_priorities = {}
            for rid, (rule, st, _h, _r, _ev) in results.items():
                if st.present and bool(rule.get("enabled", True)):
                    present_priorities[rid] = int(rule.get("priority", 50))

            mode = str(group.get("mode", "parallel"))
            for rule, st in fire_on:
                rid = str(rule.get("id", ""))
                if mode == "mutex":
                    prio = int(rule.get("priority", 50))
                    others = [p for k, p in present_priorities.items() if k != rid]
                    if others and max(others) > prio:
                        continue
                fire_rule(rule, st)

            for _rid, (rule, st, _hit, _res, ev) in results.items():
                if (not ev or not st.present or _rid in member_link
                        or not rule.get("enabled", True)):
                    continue
                if st.presence_fires <= 0 or st.last_fire >= now:
                    continue
                fire_rule(rule, st)

            for link in links:
                lid = str(link.get("id", ""))
                members = [m for m in link.get("members", []) if m in results]
                present = [m for m in members if results[m][1].present]
                typ = str(link.get("type", "exclusive"))
                if typ == "exclusive":
                    best = None
                    if present:
                        best = max(present, key=lambda m: (int(results[m][0].get("priority", 50)),
                                                           float(results[m][1].score)))
                    if best and self._link_best.get(lid) != best:
                        fire_rule(results[best][0], results[best][1])
                    self._link_best[lid] = best
                elif typ == "all":
                    if members and len(present) == len(members):
                        if not self._link_fired.get(lid):
                            for m in members:
                                fire_rule(results[m][0], results[m][1])
                            self._link_fired[lid] = True
                    else:
                        self._link_fired[lid] = False
                elif typ == "sequence":
                    step = int(self._link_step.get(lid, 0) or 0)
                    if not present:
                        step = 0
                    elif step < len(members):
                        m = members[step]
                        if results[m][1].present:
                            fire_rule(results[m][0], results[m][1])
                            step += 1
                    if step >= len(members):
                        step = 0
                    self._link_step[lid] = step

            for rule, st in fire_off:
                cooldown = max(0, int(rule.get("miss_cooldown_ms",
                                               rule.get("cooldown_ms", 0)) or 0)) / 1000.0
                if now - st.last_fire < cooldown:
                    continue
                st.last_fire = now
                self._fire(rule, rule.get("miss_actions"), (0, 0))

            # 组级：所有常驻规则都不存在
            if group.get("on_none_present"):
                any_present = any(st.present for (_r, st, _h, _res, _ev) in results.values())
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
                        self._fire({"id": ""}, group.get("on_none_present"), (0, 0))

            ids = {str(r.get("id")) for r in rules}
            for rid in list(self._states):
                if rid not in ids:
                    self._states.pop(rid, None)

            self._stop.wait(max(0.05, int(group.get("poll_ms", 150)) / 1000.0))
        self._close_source()

    def status(self) -> list[dict]:
        out = []
        now = time.time()
        for rid, st in list(self._states.items()):
            if st.present:
                present_s = max(0.0, now - (st.hit_since or st.created_at))
                absent_s = 0.0
            else:
                present_s = 0.0
                absent_s = max(0.0, now - (st.miss_since or st.created_at))
            out.append({"id": rid, "present": st.present, "score": st.score,
                        "error": st.error, "cost_ms": st.cost_ms,
                        "present_s": present_s, "absent_s": absent_s,
                        "count": st.count})
        return out
