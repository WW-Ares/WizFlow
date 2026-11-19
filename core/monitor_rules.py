"""多图标监控：规则模型 + 匹配调度引擎。"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from .actions import ActionContext, execute_actions
from .log import log
from .capture import make_source
from .screen import MatchResult, TemplateMatcher, grab

DEFAULTS_RULE = {
    "name": "规则",
    "enabled": True,
    "template": "",
    "template_rect": [],
    "roi": [],
    "threshold": 0.85,
    "multiscale": True,
    "priority": 50,
    "cooldown_ms": 1000,
    "stable_frames": 1,
    "require_absence": True,
    "max_triggers": 0,
    "group": "",
    "recheck": True,
    "actions": [],
}

DEFAULTS_GROUP = {
    "name": "监控组",
    "region": [0, 0, 0, 0],
    "poll_ms": 150,
    "match_mode": "any",
    "max_actions_per_tick": 1,
    "run_mode": "continuous",
    "timeout_ms": 0,
    "stop_on_match": False,
    "capture_mode": "screen",
    "window_title": "",
    "window_class": "",
    "window_process": "",
    "window_hwnd": 0,
    "rules": [],
    "links": [],
}


def _i(v, d=0):
    try:
        return int(float(v))
    except Exception:
        return d


def _f(v, d=0.0):
    try:
        return float(v)
    except Exception:
        return d


def normalize_rule(d: dict, idx: int = 0) -> dict:
    out = dict(DEFAULTS_RULE)
    d = d or {}
    out.update(d)
    out["id"] = str(d.get("id") or ("r%d" % (idx + 1)))
    out["name"] = str(d.get("name", "规则%d" % (idx + 1)))
    out["template"] = str(d.get("template", "") or "")
    out["template_rect"] = [_i(v) for v in (d.get("template_rect") or [])][:4]
    out["roi"] = [_i(v) for v in (d.get("roi") or [])][:4]
    out["threshold"] = max(0.1, min(0.999, _f(d.get("threshold", 0.85), 0.85)))
    out["multiscale"] = bool(d.get("multiscale", True))
    out["priority"] = _i(d.get("priority", 50), 50)
    out["cooldown_ms"] = max(0, _i(d.get("cooldown_ms", 1000), 1000))
    out["stable_frames"] = max(1, _i(d.get("stable_frames", 1), 1))
    out["require_absence"] = bool(d.get("require_absence", True))
    out["max_triggers"] = max(0, _i(d.get("max_triggers", 0), 0))
    out["group"] = str(d.get("group", "") or "")
    out["recheck"] = bool(d.get("recheck", True))
    actions = d.get("actions") or []
    out["actions"] = [a for a in actions if isinstance(a, dict)]
    return out


def normalize_link(d: dict, idx: int = 0) -> dict:
    d = d or {}
    return {
        "id": str(d.get("id") or ("g%d" % (idx + 1))),
        "name": str(d.get("name", "关联组%d" % (idx + 1))),
        "type": str(d.get("type", "exclusive")),
        "members": [str(v) for v in (d.get("members") or [])],
        "priority": _i(d.get("priority", 50), 50),
        "timeout_ms": max(0, _i(d.get("timeout_ms", 3000), 3000)),
        "actions": [a for a in (d.get("actions") or []) if isinstance(a, dict)],
    }


def normalize_monitor_group(d: dict) -> dict:
    out = dict(DEFAULTS_GROUP)
    d = d or {}
    out.update(d)
    out["name"] = str(d.get("name", "监控组"))
    out["region"] = [_i(v) for v in (d.get("region") or [0, 0, 0, 0])][:4]
    out["poll_ms"] = max(20, _i(d.get("poll_ms", 150), 150))
    out["match_mode"] = str(d.get("match_mode", "any"))
    out["max_actions_per_tick"] = max(0, _i(d.get("max_actions_per_tick", 1), 1))
    out["run_mode"] = str(d.get("run_mode", "continuous"))
    out["timeout_ms"] = max(0, _i(d.get("timeout_ms", 0), 0))
    out["stop_on_match"] = bool(d.get("stop_on_match", False))
    out["capture_mode"] = str(d.get("capture_mode", "screen"))
    out["window_title"] = str(d.get("window_title", "") or "")
    out["window_class"] = str(d.get("window_class", "") or "")
    out["window_process"] = str(d.get("window_process", "") or "")
    out["window_hwnd"] = _i(d.get("window_hwnd", 0), 0)
    rules = []
    seen = set()
    for i, r in enumerate(d.get("rules") or []):
        nr = normalize_rule(r, i)
        while nr["id"] in seen:
            nr["id"] += "_"
        seen.add(nr["id"])
        rules.append(nr)
    out["rules"] = rules
    links = []
    for i, l in enumerate(d.get("links") or []):
        links.append(normalize_link(l, i))
    out["links"] = links
    by_id = {r["id"]: r for r in rules}
    for l in links:
        l["members"] = [rid for rid in l["members"] if rid in by_id]
    for r in rules:
        if r["group"] and not any(l["id"] == r["group"] for l in links):
            r["group"] = ""
    return out


def group_to_event(d: dict) -> dict:
    ev = normalize_monitor_group(d)
    ev["type"] = "monitor_group"
    ev["delay"] = 0
    return ev


def _abs_roi(region, roi):
    x, y, w, h = [int(v) for v in region]
    if len(roi) == 4 and roi[2] > 0 and roi[3] > 0:
        return x + int(roi[0]), y + int(roi[1]), int(roi[2]), int(roi[3])
    return x, y, w, h


def match_rule(matcher: TemplateMatcher, frame, region, rule, origin=(0, 0)) -> MatchResult:
    ox, oy = int(origin[0]), int(origin[1])
    if not rule.get("template"):
        return MatchResult()
    x, y, w, h = [int(v) for v in region]
    roi = rule.get("roi") or []
    if len(roi) == 4 and roi[2] > 0 and roi[3] > 0:
        rx = max(0, int(roi[0]))
        ry = max(0, int(roi[1]))
        rw = min(int(roi[2]), max(1, frame.shape[1] - rx))
        rh = min(int(roi[3]), max(1, frame.shape[0] - ry))
        sub = frame[ry:ry + rh, rx:rx + rw]
        res = matcher.find(sub, rule["template"], rule["threshold"], rule["multiscale"])
        res.x += x + rx + ox
        res.y += y + ry + oy
        return res
    res = matcher.find(frame, rule["template"], rule["threshold"], rule["multiscale"])
    res.x += x + ox
    res.y += y + oy
    return res


@dataclass
class RuleState:
    streak: int = 0
    prev_hit: bool = False
    last_fire: float = 0.0
    count: int = 0
    last_match: MatchResult = field(default_factory=MatchResult)


class MonitorEngine:
    def __init__(self, matcher: TemplateMatcher):
        self.matcher = matcher

    def run(self, group: dict, player, script_dir: Path, stop_event, pause_event,
            on_status) -> str:
        group = normalize_monitor_group(group)
        try:
            source = make_source(group)
        except Exception as exc:
            on_status("抓屏源不可用: %s" % exc, None)
            return "capture_error"
        try:
            return self._run_inner(group, player, script_dir, stop_event, pause_event,
                                   on_status, source)
        finally:
            source.close()

    def _run_inner(self, group: dict, player, script_dir: Path, stop_event, pause_event,
                   on_status, source) -> str:
        group = normalize_monitor_group(group)
        is_screen = getattr(source, "mode", "screen") == "screen"
        origin = (0, 0) if is_screen else source.origin

        def _grab_frame(rect):
            return grab(rect) if is_screen else source.grab(rect)

        region = [int(v) for v in group["region"]]
        if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
            on_status("监控组区域无效", None)
            return "invalid_region"
        rules = group["rules"]
        links = group["links"]
        if not rules:
            on_status("监控组没有规则", None)
            return "empty"
        by_id = {r["id"]: r for r in rules}
        states = {r["id"]: RuleState() for r in rules}
        link_states = {l["id"]: {"step": 0, "deadline": 0.0, "prev_hit": False} for l in links}
        gseq = {"idx": 0, "deadline": 0.0}
        t0 = time.perf_counter()
        on_status("多图标监控启动：%d 条规则" % len(rules), None)

        while not stop_event.is_set():
            if pause_event.is_set():
                time.sleep(0.05)
                continue
            try:
                frame = _grab_frame(region)
            except Exception as exc:
                on_status("监控抓屏失败: %s" % exc, None)
                time.sleep(0.5)
                continue
            now = time.perf_counter() * 1000.0
            results = {}
            for rule in rules:
                if not rule.get("enabled", True):
                    results[rule["id"]] = MatchResult()
                    continue
                try:
                    res = match_rule(self.matcher, frame, region, rule, origin)
                except Exception as exc:
                    log("规则匹配失败 %s: %s" % (rule["name"], exc), "WARN")
                    res = MatchResult()
                results[rule["id"]] = res
                st = states[rule["id"]]
                if res.score >= rule["threshold"]:
                    st.streak += 1
                    st.last_match = res
                else:
                    st.streak = 0

            def eligible(rule):
                st = states[rule["id"]]
                if not rule.get("enabled", True):
                    return False
                if st.streak < rule["stable_frames"]:
                    return False
                if rule["require_absence"] and st.prev_hit:
                    return False
                if st.last_fire and (now - st.last_fire) < rule["cooldown_ms"]:
                    return False
                if rule["max_triggers"] and st.count >= rule["max_triggers"]:
                    return False
                return True

            def cand_rule(rule, res):
                return {"kind": "rule", "executions": [(rule["actions"], res, rule["name"])],
                        "priority": rule["priority"], "score": res.score,
                        "order": rules.index(rule), "rules": [rule], "match": res}

            candidates = []
            linked = set()
            for link in links:
                linked.update(link["members"])
            for idx, rule in enumerate(rules):
                if rule["id"] in linked:
                    continue
                if eligible(rule) and results[rule["id"]].score >= rule["threshold"]:
                    candidates.append(cand_rule(rule, results[rule["id"]]))

            for link in links:
                members = [by_id[r] for r in link["members"] if r in by_id and by_id[r].get("enabled", True)]
                if not members:
                    continue
                typ = link["type"]
                if typ == "exclusive":
                    hits = [r for r in members
                            if results[r["id"]].score >= r["threshold"] and eligible(r)]
                    if hits:
                        best = max(hits, key=lambda r: (r["priority"], results[r["id"]].score))
                        candidates.append(cand_rule(best, results[best["id"]]))
                elif typ == "all":
                    ok = (all(results[r["id"]].score >= r["threshold"] for r in members)
                          and all(eligible(r) for r in members))
                    if ok:
                        if link["actions"]:
                            executions = [(link["actions"], results[members[0]["id"]], link["name"])]
                        else:
                            executions = [(r["actions"], results[r["id"]], r["name"]) for r in members]
                        maxp = max(r["priority"] for r in members)
                        candidates.append({"kind": "link", "executions": executions,
                                           "priority": link["priority"] or maxp,
                                           "score": max(results[r["id"]].score for r in members),
                                           "order": rules.index(members[0]), "rules": members,
                                           "match": results[members[0]["id"]]})
                elif typ == "sequence":
                    if link_states[link["id"]]["deadline"] and now > link_states[link["id"]]["deadline"]:
                        link_states[link["id"]]["step"] = 0
                        link_states[link["id"]]["deadline"] = 0.0
                    step = link_states[link["id"]]["step"]
                    if step >= len(members):
                        step = 0
                        link_states[link["id"]]["step"] = 0
                    r = members[step]
                    if results[r["id"]].score >= r["threshold"] and eligible(r):
                        candidates.append(cand_rule(r, results[r["id"]]))
                        link_states[link["id"]]["step"] = step + 1
                        link_states[link["id"]]["deadline"] = now + (link["timeout_ms"] or 3000)

            mode = group["match_mode"]
            if mode == "priority":
                candidates = candidates[:1]
            elif mode == "all":
                all_ok = all(results[r["id"]].score >= r["threshold"] for r in rules if r.get("enabled", True))
                if not all_ok:
                    candidates = []
            elif mode == "sequence":
                order = sorted(rules, key=lambda r: -r["priority"])
                if gseq["idx"] >= len(order) or (gseq["deadline"] and now > gseq["deadline"]):
                    gseq["idx"] = 0
                if gseq["idx"] < len(order):
                    r = order[gseq["idx"]]
                    if results[r["id"]].score >= r["threshold"] and eligible(r):
                        candidates = [cand_rule(r, results[r["id"]])]
                        gseq["idx"] += 1
                        gseq["deadline"] = now + 3000
                    else:
                        candidates = []
                else:
                    gseq["idx"] = 0
                    candidates = []

            candidates.sort(key=lambda c: (-c["priority"], -c["score"], c["order"]))
            limit = group["max_actions_per_tick"]
            if limit and limit > 0:
                candidates = candidates[:limit]

            fired_this = False
            for cand in candidates:
                if stop_event.is_set():
                    break
                if any(r.get("recheck", True) for r in cand["rules"]):
                    try:
                        f2 = _grab_frame(region)
                        ok_all = True
                        for r in cand["rules"]:
                            fr = match_rule(self.matcher, f2, region, r, origin)
                            if fr.score < r["threshold"]:
                                ok_all = False
                                break
                        if not ok_all:
                            on_status("规则失效，已跳过：%s" % " / ".join(r["name"] for r in cand["rules"]), None)
                            continue
                        new_exec = []
                        for (act, _m, _n), r in zip(cand["executions"], cand["rules"]):
                            new_exec.append((act, match_rule(self.matcher, f2, region, r, origin), r["name"]))
                        cand["executions"] = new_exec
                    except Exception:
                        pass
                for actions, match, rule_name in cand["executions"]:
                    if stop_event.is_set():
                        break
                    ctx = ActionContext(rule_name, match, (int(origin[0]) + region[0], int(origin[1]) + region[1], region[2], region[3]), frame, script_dir,
                                        cand["rules"][0])
                    r = execute_actions(player, actions, ctx)
                    if r == "stopped":
                        return "stopped"
                    if r == "stop_script":
                        return "stop_script"
                    if r == "stop_monitor":
                        return "ok"
                for r in cand["rules"]:
                    st = states[r["id"]]
                    st.last_fire = now
                    st.count += 1
                    st.prev_hit = True
                    on_status("命中：%s  score=%.3f  @(%d,%d)"
                              % (r["name"], results[r["id"]].score,
                                 results[r["id"]].center[0], results[r["id"]].center[1]), None)
                    log("monitor rule=%s score=%.3f at=%s"
                        % (r["name"], results[r["id"]].score, results[r["id"]].center))
                fired_this = True
                if group["stop_on_match"]:
                    return "ok"

            for r in rules:
                st = states[r["id"]]
                st.prev_hit = results[r["id"]].score >= r["threshold"]

            if group["run_mode"] == "once" and fired_this:
                return "ok"
            if group["timeout_ms"] > 0 and (time.perf_counter() - t0) * 1000.0 >= group["timeout_ms"]:
                on_status("监控组超时结束", None)
                return "timeout"
            stop_event.wait(max(0.02, group["poll_ms"] / 1000.0))
        return "stopped"
