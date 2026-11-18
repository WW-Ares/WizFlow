"""多图标监控引擎回归测试（不注入真实键鼠）。"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

import core.monitor_rules as mr
from core.monitor_rules import MonitorEngine
from core.player import Player
from core.screen import TemplateMatcher, save_png


def make_frame():
    frame = np.zeros((220, 360, 3), np.uint8)
    cv2.rectangle(frame, (40, 40), (100, 100), (0, 220, 255), -1)
    cv2.circle(frame, (70, 70), 10, (20, 20, 255), -1)
    cv2.rectangle(frame, (220, 110), (300, 190), (80, 220, 80), -1)
    cv2.putText(frame, "B", (245, 165), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    return frame


def make_templates(tmp):
    f = make_frame()
    a = tmp / "a.png"
    b = tmp / "b.png"
    save_png(f[40:101, 40:101], a)
    save_png(f[110:191, 220:301], b)
    return str(a), str(b)


def make_player(moves):
    p = Player(TemplateMatcher(), lambda t, pr: None, lambda r: None)
    p.dry_run = True
    p._move = lambda pos: moves.append(tuple(pos))
    return p


def base_group(a, b, actions_a, actions_b, max_actions=1, run_mode="once", links=None):
    return {
        "name": "test", "region": [0, 0, 360, 220], "poll_ms": 30,
        "match_mode": "any", "max_actions_per_tick": max_actions,
        "run_mode": run_mode, "timeout_ms": 2000, "stop_on_match": False,
        "rules": [
            {"id": "a", "name": "A", "template": a, "threshold": 0.9,
             "priority": 100, "cooldown_ms": 0, "stable_frames": 1,
             "require_absence": True, "actions": actions_a, "group": "",
             "recheck": False},
            {"id": "b", "name": "B", "template": b, "threshold": 0.9,
             "priority": 50, "cooldown_ms": 0, "stable_frames": 1,
             "require_absence": True, "actions": actions_b, "group": "",
             "recheck": False},
        ],
        "links": links or [],
    }


def test_order():
    tmp = Path(tempfile.mkdtemp(prefix="wiz_mr_"))
    a, b = make_templates(tmp)
    move = [{"type": "move", "position": "center"}]
    g = base_group(a, b, move, move, max_actions=1)
    moves = []
    p = make_player(moves)
    old = mr.grab
    mr.grab = lambda region: make_frame()
    try:
        r = MonitorEngine(TemplateMatcher()).run(g, p, tmp, threading.Event(), threading.Event(), lambda *x: None)
    finally:
        mr.grab = old
    assert r in ("ok", "timeout"), r
    assert len(moves) == 1, moves
    assert abs(moves[0][0] - 70) <= 3 and abs(moves[0][1] - 70) <= 3, moves
    return "priority A first"


def test_two_actions():
    tmp = Path(tempfile.mkdtemp(prefix="wiz_mr_"))
    a, b = make_templates(tmp)
    move = [{"type": "move", "position": "center"}]
    g = base_group(a, b, move, move, max_actions=2)
    moves = []
    p = make_player(moves)
    old = mr.grab
    mr.grab = lambda region: make_frame()
    try:
        MonitorEngine(TemplateMatcher()).run(g, p, tmp, threading.Event(), threading.Event(), lambda *x: None)
    finally:
        mr.grab = old
    assert len(moves) == 2, moves
    assert moves[0][1] < moves[1][1], moves
    return "A then B"


def test_exclusive():
    tmp = Path(tempfile.mkdtemp(prefix="wiz_mr_"))
    a, b = make_templates(tmp)
    move = [{"type": "move", "position": "center"}]
    g = base_group(a, b, move, move, max_actions=2)
    g["rules"][0]["group"] = "g1"
    g["rules"][1]["group"] = "g1"
    g["links"] = [{"id": "g1", "name": "g1", "type": "exclusive", "members": ["a", "b"], "priority": 10}]
    moves = []
    p = make_player(moves)
    old = mr.grab
    mr.grab = lambda region: make_frame()
    try:
        MonitorEngine(TemplateMatcher()).run(g, p, tmp, threading.Event(), threading.Event(), lambda *x: None)
    finally:
        mr.grab = old
    assert len(moves) == 1 and abs(moves[0][0] - 70) <= 3, moves
    return "exclusive keeps A"


def test_all_link():
    tmp = Path(tempfile.mkdtemp(prefix="wiz_mr_"))
    a, b = make_templates(tmp)
    move = [{"type": "move", "position": "center"}]
    g = base_group(a, b, move, move, max_actions=2)
    g["rules"][0]["group"] = "g1"
    g["rules"][1]["group"] = "g1"
    g["links"] = [{"id": "g1", "name": "g1", "type": "all", "members": ["a", "b"], "priority": 10}]
    moves = []
    p = make_player(moves)
    old = mr.grab
    mr.grab = lambda region: make_frame()
    try:
        MonitorEngine(TemplateMatcher()).run(g, p, tmp, threading.Event(), threading.Event(), lambda *x: None)
    finally:
        mr.grab = old
    assert len(moves) == 2, moves
    return "all link executes both"


def test_require_absence():
    tmp = Path(tempfile.mkdtemp(prefix="wiz_mr_"))
    a, b = make_templates(tmp)
    move = [{"type": "move", "position": "center"}]
    g = base_group(a, b, move, move, max_actions=1, run_mode="continuous")
    moves = []
    p = make_player(moves)
    stop = threading.Event()
    old = mr.grab
    mr.grab = lambda region: make_frame()
    try:
        t = threading.Thread(target=lambda: MonitorEngine(TemplateMatcher()).run(
            g, p, tmp, stop, threading.Event(), lambda *x: None), daemon=True)
        t.start()
        time.sleep(0.6)
        stop.set()
        t.join(timeout=2)
    finally:
        mr.grab = old
    assert len(moves) == 1, moves
    return "no repeat while present"




def test_action_variants_dryrun():
    from core.actions import ActionContext, execute_actions
    from core.screen import MatchResult
    moves = []
    p = make_player(moves)
    m = MatchResult(score=0.99, x=100, y=100, w=40, h=30)
    ctx = ActionContext("test", m, (0, 0, 360, 220), None, Path(tempfile.mkdtemp()), {})
    actions = [
        {"type": "click", "button": "left", "clicks": 2, "hold_ms": 5, "interval_ms": 5},
        {"type": "click", "button": "right", "clicks": 1, "hold_ms": 5},
        {"type": "click", "button": "middle", "clicks": 1, "hold_ms": 5},
        {"type": "click", "button": "x1", "clicks": 1, "hold_ms": 5},
        {"type": "long_press", "button": "left", "hold_ms": 5},
        {"type": "key", "keys": "ctrl+a", "hold_ms": 5},
        {"type": "wait", "ms": 5},
        {"type": "notify", "text": "ok"},
        {"type": "move", "position": "center"},
    ]
    r = execute_actions(p, actions, ctx)
    assert r == "ok", r
    return "click/double/long/right/middle/x1/key ok"


def test_player_monitor_group():
    tmp = Path(tempfile.mkdtemp(prefix="wiz_mr_"))
    a, b = make_templates(tmp)
    move = [{"type": "move", "position": "center"}]
    g = base_group(a, b, move, move, max_actions=1, run_mode="once")
    moves = []
    p = make_player(moves)
    old = mr.grab
    mr.grab = lambda region: make_frame()
    try:
        ev = dict(g)
        ev["type"] = "monitor_group"
        ev["delay"] = 0
        assert p.start([ev], 1, 4.0, script_dir=tmp)
        end = time.time() + 4
        while p.running and time.time() < end:
            time.sleep(0.02)
        assert not p.running, "player still running"
    finally:
        mr.grab = old
    assert len(moves) == 1, moves
    return "player monitor_group ok"




def test_all_link_no_repeat():
    tmp = Path(tempfile.mkdtemp(prefix="wiz_mr_"))
    a, b = make_templates(tmp)
    move = [{"type": "move", "position": "center"}]
    g = base_group(a, b, move, move, max_actions=2, run_mode="continuous")
    g["rules"][0]["group"] = "g1"
    g["rules"][1]["group"] = "g1"
    g["links"] = [{"id": "g1", "name": "g1", "type": "all", "members": ["a", "b"], "priority": 10}]
    moves = []
    p = make_player(moves)
    stop = threading.Event()
    old = mr.grab
    mr.grab = lambda region: make_frame()
    try:
        t = threading.Thread(target=lambda: MonitorEngine(TemplateMatcher()).run(
            g, p, tmp, stop, threading.Event(), lambda *x: None), daemon=True)
        t.start()
        time.sleep(0.6)
        stop.set()
        t.join(timeout=2)
    finally:
        mr.grab = old
    assert len(moves) == 2, moves
    return "all link fires once while present"


def main():
    tests = [test_order, test_two_actions, test_exclusive, test_all_link, test_require_absence,
             test_action_variants_dryrun, test_player_monitor_group, test_all_link_no_repeat]
    failed = 0
    for fn in tests:
        try:
            print("PASS  %-28s %s" % (fn.__name__, fn()))
        except Exception as exc:
            failed += 1
            print("FAIL  %-28s %s" % (fn.__name__, exc))
            import traceback
            traceback.print_exc()
    print("=" * 50)
    print("ALL PASS" if not failed else "%d FAILED" % failed)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
