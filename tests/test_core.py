"""核心回归测试：不依赖界面，不注入真实键鼠。

    python tests/test_core.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

import core.screen as screen
import core.player as player_mod
from core.player import Player
from core.screen import TemplateMatcher, save_png, to_ctk_image


def make_frame():
    frame = np.zeros((150, 200, 3), np.uint8)
    cv2.rectangle(frame, (80, 60), (120, 90), (0, 220, 255), -1)
    cv2.circle(frame, (100, 75), 9, (40, 40, 255), -1)
    cv2.putText(frame, "K", (84, 86), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    return frame


def wait_player(p: Player, timeout=6.0):
    end = time.time() + timeout
    while p.running and time.time() < end:
        time.sleep(0.02)
    assert not p.running, "player did not finish"


def test_monitor_hit_and_anchor():
    tmp = Path(tempfile.mkdtemp(prefix="kmg_test_"))
    frame = make_frame()
    tpl = frame[60:91, 80:121].copy()
    tpl_path = tmp / "icon.png"
    save_png(tpl, tpl_path)

    origin = screen.grab
    origin2 = player_mod.grab
    screen.grab = lambda rect: frame.copy()
    player_mod.grab = screen.grab
    moves = []
    try:
        statuses = []
        p = Player(TemplateMatcher(), lambda t, pr: statuses.append(t), lambda r: statuses.append("DONE:" + r))
        p.dry_run = True
        p._move = lambda pos: moves.append(tuple(pos))
        ev = {
            "type": "monitor", "name": "测试图标", "region": [0, 0, 200, 150],
            "template": str(tpl_path), "threshold": 0.9, "poll_ms": 30,
            "timeout_ms": 1000, "multiscale": False, "click_sync": False,
            "on_match": [
                {"type": "event", "event_type": "EM", "delay": 0,
                 "action_type": "mouse move", "action": [5, 5], "anchor": True}
            ],
        }
        assert p.start([ev], 1, 4.0, script_dir=tmp)
        wait_player(p)
        assert any("监控命中" in s for s in statuses), statuses
        assert moves and abs(moves[0][0] - 105) <= 3 and abs(moves[0][1] - 80) <= 3, moves
        assert any(s == "DONE:finished" for s in statuses), statuses
    finally:
        screen.grab = origin
        player_mod.grab = origin2
    return "hit center + anchor ok"


def test_monitor_timeout():
    tmp = Path(tempfile.mkdtemp(prefix="kmg_test_"))
    frame = make_frame()
    # 模板取自完全不同的噪声图，必然不匹配
    rng = np.random.default_rng(7)
    bad = rng.integers(0, 255, (31, 41, 3), dtype=np.uint8)
    tpl_path = tmp / "bad.png"
    save_png(bad, tpl_path)

    origin = screen.grab
    origin2 = player_mod.grab
    screen.grab = lambda rect: frame.copy()
    player_mod.grab = screen.grab
    try:
        statuses = []
        p = Player(TemplateMatcher(), lambda t, pr: statuses.append(t), lambda r: statuses.append("DONE:" + r))
        p.dry_run = True
        ev = {
            "type": "monitor", "name": "超时测试", "region": [0, 0, 200, 150],
            "template": str(tpl_path), "threshold": 0.95, "poll_ms": 30,
            "timeout_ms": 250, "multiscale": False, "click_sync": True,
            "on_timeout": [
                {"type": "event", "event_type": "EK", "delay": 0,
                 "action_type": "key down", "action": [65, "a", 0]}
            ],
        }
        t0 = time.time()
        assert p.start([ev], 1, 2.0, script_dir=tmp)
        wait_player(p)
        assert time.time() - t0 < 5.0
        assert any("监控超时" in s for s in statuses), statuses
        assert any(s == "DONE:finished" for s in statuses), statuses
    finally:
        screen.grab = origin
        player_mod.grab = origin2
    return "timeout + on_timeout ok"


def test_hotkey_parse():
    from core.hotkeys import parse_hotkey
    assert parse_hotkey("F6") == (0, 0x75)
    assert parse_hotkey("ctrl+f9") == (0x0002, 0x78)
    assert parse_hotkey("shift+alt+a") == (0x0004 | 0x0001, ord("A"))
    assert parse_hotkey("") is None
    return "F6/ctrl+F9/alt+shift+A ok"


def test_recorder_events():
    import time as _t
    from pynput import keyboard, mouse
    from core.recorder import Recorder, _key_info

    out = []
    r = Recorder(out.append)
    r.recording = True
    r.record_trajectory = True
    r.move_interval_ms = 10
    r._last_time = _t.perf_counter()
    r._last_move_time = 0.0
    r._last_move = (0, 0)
    r._on_move(10, 10)
    r._on_move(14, 10)   # 间隔不足，丢弃
    _t.sleep(0.02)
    r._on_move(20, 10)
    r._on_click(20, 10, mouse.Button.left, True)
    r._on_click(20, 10, mouse.Button.left, False)
    r._on_scroll(20, 10, 0, 1)
    vk, name, ext = _key_info(keyboard.KeyCode.from_vk(65))
    assert vk == 65 and ext == 0, (vk, name, ext)
    r._on_press(keyboard.KeyCode.from_vk(65))
    r._on_release(keyboard.KeyCode.from_vk(65))
    r.recording = False
    kinds = [e["action_type"] for e in out]
    assert kinds.count("mouse move") == 2, kinds
    assert "mouse left down" in kinds and "mouse left up" in kinds
    assert "mouse wheel up" in kinds
    assert "key down" in kinds and "key up" in kinds
    assert all(isinstance(e["delay"], (int, float)) and e["delay"] >= 0 for e in out)
    return "%d events" % len(out)
def test_monitor_run_script_sync():
    tmp = Path(tempfile.mkdtemp(prefix="kmg_test_"))
    frame = make_frame()
    tpl = frame[60:91, 80:121].copy()
    tpl_path = tmp / "icon.png"
    save_png(tpl, tpl_path)

    from core.script import ScriptDoc
    sub = [{"type": "event", "event_type": "EM", "delay": 0,
            "action_type": "mouse move", "action": [100, 100]}]
    ScriptDoc(sub, tmp / "sub.json").save()

    origin = screen.grab
    origin2 = player_mod.grab
    screen.grab = lambda rect: frame.copy()
    player_mod.grab = screen.grab
    moves = []
    try:
        p = Player(TemplateMatcher(), lambda t, pr: None, lambda r: None)
        p.dry_run = True
        p._move = lambda pos: moves.append(tuple(pos))
        ev = {
            "type": "monitor", "name": "同步脚本", "region": [0, 0, 200, 150],
            "template": str(tpl_path), "threshold": 0.9, "poll_ms": 30,
            "timeout_ms": 1000, "multiscale": False, "click_sync": False,
            "run_script": "sub.json", "script_anchor": [100, 100],
        }
        assert p.start([ev], 1, 4.0, script_dir=tmp)
        wait_player(p)
        assert moves == [(100, 75)], moves
    finally:
        screen.grab = origin
        player_mod.grab = origin2
    return "sub-script shifted to hit center"

def test_keepalive_integrated():
    from core.vdisplay import keepalive
    assert keepalive.enable() == ""
    st = keepalive.status()
    assert st.get("enabled") is True and st.get("mode") == "integrated", st
    keepalive.disable()
    st2 = keepalive.status()
    assert st2.get("enabled") is False, st2
    return "integrated enable/disable ok"



def test_monitor_click_modes():
    tmp = Path(tempfile.mkdtemp(prefix="kmg_test_"))
    frame = make_frame()
    tpl = frame[60:91, 80:121].copy()
    tpl_path = tmp / "icon.png"
    save_png(tpl, tpl_path)
    origin = screen.grab
    origin2 = player_mod.grab
    screen.grab = lambda rect: frame.copy()
    player_mod.grab = screen.grab
    try:
        cases = [
            ("center", 100, 75, {}),
            ("fixed", 500, 400, {"fixed_x": 500, "fixed_y": 400}),
            ("follow", 110, 70, {"offset_x": 10, "offset_y": -5}),
        ]
        for mode, ex, ey, extra in cases:
            moves = []
            p = Player(TemplateMatcher(), lambda t, pr: None, lambda r: None)
            p.dry_run = True
            p._move = lambda pos: moves.append(tuple(pos))
            ev = {"type": "monitor", "name": mode, "region": [0, 0, 200, 150],
                  "template": str(tpl_path), "threshold": 0.9, "poll_ms": 30,
                  "timeout_ms": 1000, "multiscale": False, "hit_action": "mouse",
                  "click_style": "single", "click_mode": mode, "random_x": 0,
                  "random_y": 0, "click_button": "left"}
            ev.update(extra)
            assert p.start([ev], 1, 4.0, script_dir=tmp)
            wait_player(p)
            assert moves, (mode, moves)
            assert abs(moves[0][0] - ex) <= 3 and abs(moves[0][1] - ey) <= 3, (mode, moves[0])
    finally:
        screen.grab = origin
        player_mod.grab = origin2
    return "center/fixed/follow ok"



def test_monitor_key_action():
    tmp = Path(tempfile.mkdtemp(prefix="kmg_test_"))
    frame = make_frame()
    tpl = frame[60:91, 80:121].copy()
    tpl_path = tmp / "icon.png"
    save_png(tpl, tpl_path)
    origin = screen.grab
    origin2 = player_mod.grab
    screen.grab = lambda rect: frame.copy()
    player_mod.grab = screen.grab
    try:
        p = Player(TemplateMatcher(), lambda t, pr: None, lambda r: None)
        p.dry_run = True
        ev = {"type": "monitor", "name": "key", "region": [0, 0, 200, 150],
              "template": str(tpl_path), "threshold": 0.9, "poll_ms": 30,
              "timeout_ms": 1000, "multiscale": False, "hit_action": "key",
              "key_combo": "ctrl+a", "key_hold_ms": 5}
        assert p.start([ev], 1, 4.0, script_dir=tmp)
        wait_player(p)
    finally:
        screen.grab = origin
        player_mod.grab = origin2
    return "key combo action ok"

def test_crop_match():
    from core.monitor import MonitorProfile, ProbeResult, crop_match
    frame = make_frame()
    prof = MonitorProfile(region=[10, 10, 200, 150], template="x")
    pr = ProbeResult(score=0.99, matched=True, rect=(90, 70, 41, 31), frame=frame)
    crop = crop_match(pr, prof)
    assert crop is not None and crop.shape[:2] == (31, 41), None if crop is None else crop.shape
    return "crop ok"


def test_preview_conversion():
    pil = to_ctk_image(make_frame())
    assert pil.size == (200, 150)
    return "pil ok"




def test_event_describe():
    from core.script import describe_event
    d = describe_event({"type": "event", "event_type": "EM", "action_type": "mouse right up",
                        "action": [10, 20], "delay": 5, "note": "备注"})
    assert d["kind"] == "鼠标" and d["action"] == "右键抬起", d
    assert "X=10" in d["params"] and d["note"] == "备注", d
    d = describe_event({"type": "event", "event_type": "EK", "action_type": "key down",
                        "action": [65, "a", 0]})
    assert d["action"] == "按键按下" and d["params"] == "A", d
    d = describe_event({"type": "monitor_group", "rules": [1, 2], "match_mode": "all"})
    assert d["kind"] == "监控组" and "2 条规则" in d["params"], d
    d = describe_event({"type": "wait", "ms": 500})
    assert d["kind"] == "等待" and "500" in d["params"], d
    return "mouse/key/group/wait cn ok"


def test_wait_and_script_node():
    tmp = Path(tempfile.mkdtemp(prefix="kmg_test_"))
    sub = tmp / "sub.json"
    sub.write_text('{"scripts":[{"type":"event","event_type":"EM","delay":0,'
                   '"action_type":"mouse move","action":[30,40]}]}', encoding="utf-8")
    moves = []
    p = Player(TemplateMatcher(), lambda t, pr: None, lambda r: None)
    p.dry_run = True
    p._move = lambda pos: moves.append(tuple(pos))
    evs = [{"type": "wait", "ms": 30, "delay": 0},
           {"type": "script", "path": "sub.json", "wait": True, "delay": 0}]
    assert p.start(evs, 1, 4.0, script_dir=tmp)
    wait_player(p)
    assert moves == [(30, 40)], moves
    return "wait + script node ok"


def test_parsec_bundle():
    from core import vdisplay_parsec as vp
    assert vp.driver_files_ok(), vp.driver_dir()
    st = vp.status()
    assert "installed" in st and "active" in st, st
    return "driver files ok, installed=%s active=%s" % (st["installed"], st["active"])

def main():
    tests = [test_monitor_hit_and_anchor, test_monitor_timeout, test_monitor_click_modes,
             test_monitor_key_action, test_hotkey_parse,
             test_recorder_events, test_monitor_run_script_sync, test_keepalive_integrated,
             test_crop_match,
             test_preview_conversion,
             test_event_describe, test_wait_and_script_node, test_parsec_bundle]
    failed = 0
    for fn in tests:
        try:
            print("PASS  %-30s %s" % (fn.__name__, fn()))
        except Exception as exc:
            failed += 1
            print("FAIL  %-30s %s" % (fn.__name__, exc))
            import traceback
            traceback.print_exc()
    print("=" * 50)
    print("ALL PASS" if failed == 0 else "%d FAILED" % failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())




