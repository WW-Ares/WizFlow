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
from config import ROOT
from .monitor_rules import (match_rule, normalize_monitor_group,
                            resolve_rule_templates, rule_has_template)
from .screen import TemplateMatcher


@dataclass
class RuleState:
    present: bool = False
    hit_streak: int = 0
    miss_streak: int = 0
    hit_since: float = 0.0
    miss_since: float = 0.0
    present_s: float = 0.0
    absent_s: float = 0.0
    present_since: float = 0.0
    absent_since: float = 0.0
    presence_fires: int = 0
    absence_fires: int = 0
    ever_present: bool = False
    last_miss_fire: float = 0.0
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
        self._link_step = {}
        self._link_deadline = {}
        self._seq_idx = 0
        self._seq_deadline = 0.0
        self.enabled = False
        self.grab_ms = 0.0

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        # 恢复时重设计时基准，暂停期间的时间不计入命中/消失时长
        now = time.time()
        for st in self._states.values():
            if st.present and st.present_since:
                st.present_since = now - st.present_s
            elif (not st.present) and st.absent_since:
                st.absent_since = now - st.absent_s
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

    def _can_fire(self, rule: dict, st: RuleState, now: float) -> bool:
        cooldown = max(0, int(rule.get("cooldown_ms", 0) or 0)) / 1000.0
        if now - st.last_fire < cooldown:
            return False
        fires = max(0, int(rule.get("fires_per_present", 1) or 0))
        if fires and st.presence_fires >= fires:
            return False
        return True

    def _fire_rule(self, rule: dict, st: RuleState, now: float) -> bool:
        if not self._can_fire(rule, st, now):
            return False
        st.last_fire = now
        st.count += 1
        st.presence_fires += 1
        self._fire(rule, rule.get("actions"), st.center)
        return True

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
            raw_rules = [r for r in group.get("rules", [])
                         if bool(r.get("enabled", True)) and bool(r.get("resident", False))]
            rules = []
            for r in raw_rules:
                rr = resolve_rule_templates(r, (ROOT,))
                if not rule_has_template(rr):
                    st = self._state(str(rr.get("id")))
                    st.error = "模板不存在"
                    st.present = False
                    continue
                rules.append(rr)
            self.enabled = bool(rules)
            active_ids = {str(r.get("id")) for r in rules}
            for rid, st in list(self._states.items()):
                if rid in active_ids:
                    continue
                # 不参与监控时冻结并重置计时，避免重新启用后时长跳变
                st.present_s = 0.0
                st.absent_s = 0.0
                if st.present:
                    st.present_since = time.time()
                    st.absent_since = 0.0
                else:
                    st.present_since = 0.0
                    st.absent_since = time.time() if st.miss_since else 0.0
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
                    if st.absent_since == 0:
                        st.absent_since = now
                    st.hit_since = 0
                    st.miss_streak += 1
                    st.hit_streak = 0

                if st.present:
                    st.present_s = max(0.0, now - st.present_since) if st.present_since else 0.0
                    st.absent_s = 0.0
                else:
                    st.absent_s = max(0.0, now - st.absent_since) if st.absent_since else 0.0
                    st.present_s = 0.0

            links = [l for l in (group.get("links") or []) if isinstance(l, dict)]
            member_link = {}
            for link in links:
                for rid in link.get("members", []):
                    member_link[str(rid)] = link

            # 先根据“命中时长/消失时长”更新出现状态；动作候选下面统一筛选
            link_ids = {str(l.get("id", "")) for l in links}
            for d in (self._link_step, self._link_deadline):
                for lid in list(d):
                    if lid not in link_ids:
                        d.pop(lid, None)

            for rid, (rule, st, hit, res, evaluated) in results.items():
                if not evaluated:
                    continue
                present_ms = max(0, int(rule.get("present_ms", 0) or 0))
                absent_ms = max(0, int(rule.get("absent_ms", 600) or 0))
                if (not st.present and hit and st.hit_since
                        and (now - st.hit_since) * 1000.0 >= present_ms):
                    st.present = True
                    st.ever_present = True
                    st.presence_fires = 0
                    st.absence_fires = 0
                    st.present_since = now
                    st.absent_since = 0.0
                    st.present_s = 0.0
                elif (st.present and not hit and st.miss_since
                      and (now - st.miss_since) * 1000.0 >= absent_ms):
                    st.present = False
                    st.presence_fires = 0
                    st.absence_fires = 0
                    st.absent_since = now
                    st.present_since = 0.0
                    st.absent_s = 0.0

            by_id = {str(r.get("id")): r for r in rules}
            order_map = {str(r.get("id")): i for i, r in enumerate(rules)}
            linked = set(member_link)

            def score_of(st: RuleState) -> float:
                res = getattr(st, "last_res", None)
                if res is not None:
                    try:
                        return float(getattr(res, "score", 0.0) or 0.0)
                    except Exception:
                        pass
                return float(st.score or 0.0)

            def cand_rule(rule, st):
                rid = str(rule.get("id"))
                return {"kind": "rule", "rule": rule, "st": st,
                        "priority": int(rule.get("priority", 50) or 50),
                        "score": score_of(st), "order": order_map.get(rid, 0),
                        "rid": rid, "members": [(rule, st)], "member_count": 1}

            candidates = []
            for rule in rules:
                rid = str(rule.get("id"))
                if rid in linked:
                    continue
                st = self._state(rid)
                # 处于“存在”且未达到次数/冷却限制就允许执行；
                # 这样被每轮上限挤掉的规则会在后续轮次继续，而不是永久丢失。
                if st.present and self._can_fire(rule, st, now):
                    candidates.append(cand_rule(rule, st))

            for link in links:
                lid = str(link.get("id", ""))
                members = [by_id[rid] for rid in link.get("members", []) if rid in by_id
                           and by_id[rid].get("enabled", True)]
                if not members:
                    continue
                entries = [(r, self._state(str(r.get("id")))) for r in members]
                present = [(r, st) for r, st in entries if st.present]
                typ = str(link.get("type", "exclusive"))
                link_order = min(order_map.get(str(r.get("id")), 0) for r, _st in entries)
                if typ == "exclusive":
                    hits = [(r, st) for r, st in present if self._can_fire(r, st, now)]
                    if not hits:
                        continue
                    best_rule, best_st = max(
                        hits, key=lambda x: (int(x[0].get("priority", 50) or 50),
                                             score_of(x[1])))
                    candidates.append({
                        "kind": "link", "link": link,
                        "priority": int(best_rule.get("priority", 50) or 50),
                        "score": score_of(best_st), "order": link_order,
                        "rid": lid, "members": [(best_rule, best_st)],
                        "member_count": len(entries), "actions": None,
                    })
                elif typ == "all":
                    if (entries and len(present) == len(entries)
                            and all(self._can_fire(r, st, now) for r, st in entries)):
                        maxp = max(int(r.get("priority", 50) or 50) for r, _st in entries)
                        candidates.append({
                            "kind": "link", "link": link,
                            "priority": int(link.get("priority", 0) or maxp),
                            "score": max(score_of(st) for _r, st in entries),
                            "order": link_order, "rid": lid,
                            "members": entries, "member_count": len(entries),
                            "actions": link.get("actions") or None,
                        })
                elif typ == "sequence":
                    step = int(self._link_step.get(lid, 0) or 0)
                    deadline = float(self._link_deadline.get(lid, 0.0) or 0.0)
                    if deadline and now > deadline:
                        step = 0
                        deadline = 0.0
                    if step >= len(entries):
                        step = 0
                        deadline = 0.0
                    if step < len(entries):
                        r, st = entries[step]
                        if st.present and self._can_fire(r, st, now):
                            candidates.append({
                                "kind": "link", "link": link,
                                "priority": int(r.get("priority", 50) or 50),
                                "score": score_of(st), "order": link_order,
                                "rid": lid, "members": [(r, st)],
                                "member_count": len(entries), "actions": None,
                            })
                        elif st.present:
                            fires = max(0, int(r.get("fires_per_present", 1) or 0))
                            # 当前成员次数已用完：跳过它，避免关联组顺序永久卡住
                            if fires and st.presence_fires >= fires:
                                step += 1
                    if step >= len(entries):
                        step = 0
                        deadline = 0.0
                    self._link_step[lid] = step
                    self._link_deadline[lid] = deadline

            candidates.sort(key=lambda c: (-c["priority"], -c["score"], c["order"]))
            mode = str(group.get("match_mode", "any") or "any").lower()
            seq_rules = []
            if mode == "priority":
                candidates = candidates[:1]
            elif mode == "all":
                if not all(self._state(str(r.get("id"))).present for r in rules):
                    candidates = []
            elif mode == "sequence":
                seq_rules = sorted(
                    rules, key=lambda r: (-int(r.get("priority", 50) or 50),
                                          order_map.get(str(r.get("id")), 0)))
                if (self._seq_idx >= len(seq_rules)
                        or (self._seq_deadline and now > self._seq_deadline)):
                    self._seq_idx = 0
                    self._seq_deadline = 0.0
                if self._seq_idx < len(seq_rules):
                    target = seq_rules[self._seq_idx]
                    target_id = str(target.get("id"))
                    picked = [c for c in candidates
                              if target_id in {str(r.get("id")) for r, _st in c["members"]}]
                    if picked:
                        candidates = picked[:1]
                    else:
                        candidates = []
                        st = self._state(target_id)
                        fires = max(0, int(target.get("fires_per_present", 1) or 0))
                        # 目标已经出现但次数已用完：跳过，避免顺序模式永久卡住
                        if st.present and fires and st.presence_fires >= fires:
                            self._seq_idx += 1
                else:
                    candidates = []
            else:
                # 切出顺序模式后重置游标，避免下次切回时从旧位置继续
                self._seq_idx = 0
                self._seq_deadline = 0.0

            limit = max(0, int(group.get("max_actions_per_tick", 1) or 0))
            if limit:
                candidates = candidates[:limit]

            for cand in candidates:
                if self._stop.is_set():
                    break
                fired = False
                if cand["kind"] == "rule":
                    fired = self._fire_rule(cand["rule"], cand["st"], now)
                else:
                    link = cand["link"]
                    members = cand["members"]
                    actions = cand.get("actions")
                    if actions:
                        for _r, st in members:
                            st.last_fire = now
                            st.count += 1
                            st.presence_fires += 1
                        center = members[0][1].center if members else (0, 0)
                        self._fire(link, actions, center)
                        fired = True
                    else:
                        for r, st in members:
                            if self._fire_rule(r, st, now):
                                fired = True
                    if str(link.get("type", "")) == "sequence" and fired:
                        lid = str(link.get("id", ""))
                        step = int(self._link_step.get(lid, 0) or 0) + 1
                        total = max(1, int(cand.get("member_count", 1) or 1))
                        if step >= total:
                            step = 0
                            self._link_deadline[lid] = 0.0
                        else:
                            timeout = max(0, int(link.get("timeout_ms", 3000) or 3000)) / 1000.0
                            self._link_deadline[lid] = now + timeout
                        self._link_step[lid] = step
                if fired and mode == "sequence":
                    self._seq_idx += 1
                    if self._seq_idx >= len(seq_rules):
                        self._seq_idx = 0
                        self._seq_deadline = 0.0
                    else:
                        self._seq_deadline = now + 3.0

            for _rid, (rule, st, _hit, _res, ev) in results.items():
                if (not ev or st.present or not st.ever_present
                        or not rule.get("enabled", True)):
                    continue
                fires = max(0, int(rule.get("fires_per_absence", 1) or 0))
                if fires and st.absence_fires >= fires:
                    continue
                cooldown = max(0, int(rule.get("miss_cooldown_ms",
                                               rule.get("cooldown_ms", 0)) or 0)) / 1000.0
                if now - st.last_miss_fire < cooldown:
                    continue
                st.last_miss_fire = now
                st.absence_fires += 1
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
        for rid, st in list(self._states.items()):
            # 使用引擎线程写入的稳定时长，暂停/停止时自然冻结
            present_s = max(0.0, float(st.present_s)) if st.present else 0.0
            absent_s = 0.0 if st.present else max(0.0, float(st.absent_s))
            out.append({"id": rid, "present": st.present, "score": st.score,
                        "error": st.error, "cost_ms": st.cost_ms,
                        "present_s": present_s, "absent_s": absent_s,
                        "count": st.count})
        return out
