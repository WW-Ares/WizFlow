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
from core.script import ScriptDoc
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



def test_wheel_action():
    tmp = Path(tempfile.mkdtemp(prefix="kmg_test_"))
    frame = make_frame()
    tpl_path = tmp / "icon.png"
    save_png(frame[60:91, 80:121].copy(), tpl_path)
    origin = screen.grab
    origin2 = player_mod.grab
    old_wheel = player_mod.mouse_wheel
    calls = []
    screen.grab = lambda rect: frame.copy()
    player_mod.grab = screen.grab
    player_mod.mouse_wheel = lambda d: calls.append(d)
    try:
        p = Player(TemplateMatcher(), lambda t, pr: None, lambda r: None)
        p.dry_run = False
        p._move = lambda pos: None
        ev = {"type": "monitor", "name": "wheel", "region": [0, 0, 200, 150],
              "template": str(tpl_path), "threshold": 0.9, "poll_ms": 30,
              "timeout_ms": 1000, "multiscale": False, "hit_action": "mouse",
              "click_button": "middle", "click_style": "wheel_down", "wheel_amount": 4,
              "wheel_interval_ms": 0, "click_mode": "center"}
        assert p.start([ev], 1, 4.0, script_dir=tmp)
        wait_player(p)
        assert calls == [-120, -120, -120, -120], calls
    finally:
        screen.grab = origin
        player_mod.grab = origin2
        player_mod.mouse_wheel = old_wheel
    return "wheel down x4 ok"


def test_wheel_multi_action():
    from core.actions import ActionContext, execute_actions
    from core.screen import MatchResult
    import core.actions as actions_mod
    calls = []
    old = actions_mod.mouse_wheel
    actions_mod.mouse_wheel = lambda d: calls.append(d)
    try:
        class FP:
            dry_run = False
            _pressed_buttons = set()
            _stop = player_mod.threading.Event()
            _paused = player_mod.threading.Event()
            def _wait(self, ms):
                return True
        ctx = ActionContext("r", MatchResult(1.0, 100, 100, 20, 20), (0, 0, 200, 200), None, Path("."), {})
        r = execute_actions(FP(), [{"type": "wheel", "direction": "up", "amount": 2,
                                    "interval_ms": 0, "position": "center"}], ctx)
        assert r == "ok" and calls == [120, 120], (r, calls)
    finally:
        actions_mod.mouse_wheel = old
    return "multi-icon wheel ok"


def test_keygrab_names():
    from core.keygrab import combo_text, vk_name
    assert vk_name(0x09) == "tab" and vk_name(0x41) == "a" and vk_name(0x70) == "f1"
    assert combo_text({0x12, 0x09}, 0x09) == "alt+tab"
    assert combo_text({0x11, 0x41}, 0x41) == "ctrl+a"
    return "alt+tab / ctrl+a ok"


def test_monitor_profile_wheel_roundtrip():
    from core.monitor import MonitorProfile
    p = MonitorProfile.from_dict({"click_style": "wheel", "wheel_dir": "down",
                                  "wheel_amount": 7, "wheel_interval_ms": 25})
    d = p.to_dict()
    assert d["click_style"] == "wheel_down" and d["click_button"] == "middle", d
    assert d["wheel_amount"] == 7 and d["wheel_interval_ms"] == 25, d
    p2 = MonitorProfile.from_dict({"click_button": "middle", "click_style": "single"})
    assert p2.click_button == "middle" and p2.click_style == "single", p2
    return "profile wheel roundtrip/migration ok"



def test_window_capture():
    import tkinter as tk
    from core.capture import list_windows, make_source
    root = tk.Tk()
    root.title("WizFlowCaptureTest")
    root.geometry("600x400+120+120")
    cv = tk.Canvas(root, bg="#101820", highlightthickness=0)
    cv.pack(fill="both", expand=True)
    cv.create_rectangle(40, 40, 100, 80, fill="#00ff00", outline="")
    cv.create_rectangle(45, 45, 55, 55, fill="#ff0000", outline="")
    try:
        for _ in range(20):
            root.update(); time.sleep(0.02)
        wins = [w for w in list_windows() if w.title == "WizFlowCaptureTest"]
        assert wins, "window not found"
        w = wins[0]
        tmp = Path(tempfile.mkdtemp(prefix="kmg_cap_"))
        tpl = np.zeros((40, 60, 3), np.uint8)
        tpl[:, :] = (0, 255, 0)
        tpl[5:15, 5:15] = (0, 0, 255)
        tplp = tmp / "tpl.png"
        save_png(tpl, tplp)
        results = {}
        for mode in ("window", "window_gdi"):
            src = make_source({"capture_mode": mode, "window_hwnd": w.hwnd})
            try:
                frame = src.grab((0, 0, min(600, w.width), min(400, w.height)))
                res = TemplateMatcher().find(frame, str(tplp), 0.9, False)
                results[mode] = res.center
            finally:
                src.close()
        for mode, center in results.items():
            assert abs(center[0] - 70) <= 3 and abs(center[1] - 60) <= 3, (mode, center)
        return "wgc/gdi center=%s" % results
    finally:
        root.destroy()

def test_supervisor_edges():
    import core.supervisor as sup_mod
    from core.supervisor import ScriptSupervisor

    tmp = Path(tempfile.mkdtemp(prefix="kmg_sup_"))
    frame = make_frame()
    tpl = frame[60:91, 80:121].copy()
    tpl_path = tmp / "sup.png"
    save_png(tpl, tpl_path)

    state = {"present": False}

    class FakeSource:
        origin = (0, 0)

        def grab(self, region):
            return frame.copy() if state["present"] else np.zeros_like(frame)

        def close(self):
            pass

    orig = sup_mod.make_source
    sup_mod.make_source = lambda cfg: FakeSource()
    actions = []
    cfg = {
        "enabled": True, "template": str(tpl_path), "threshold": 0.9,
        "poll_ms": 10, "stable_frames": 2, "cooldown_ms": 0,
        "appear": "pause", "disappear": "resume", "run_script": "",
        "region": [0, 0, 200, 150], "capture_mode": "screen",
        "window_hwnd": 0, "window_title": "", "window_class": "",
        "window_process": "",
    }
    s = ScriptSupervisor(lambda: dict(cfg), lambda a, t: actions.append(a))
    try:
        s.start()
        state["present"] = True
        end = time.time() + 3
        while time.time() < end and not s.status()["present"]:
            time.sleep(0.02)
        assert s.status()["present"] is True, s.status()
        state["present"] = False
        end = time.time() + 3
        while time.time() < end and s.status()["present"]:
            time.sleep(0.02)
        time.sleep(0.1)
        assert s.status()["present"] is False, s.status()
        assert actions[:2] == ["pause", "resume"], actions
        return "appear/disappear fired %s" % actions[:2]
    finally:
        s.stop()
        s.join(timeout=2)
        sup_mod.make_source = orig


def test_pause_reasons():
    p = Player(TemplateMatcher(), lambda t, pr: None, lambda r: None)
    p.dry_run = True
    p.start([{"type": "wait", "ms": 3000}], 1, 1.0)
    try:
        assert p.pause("rule:A")
        assert p.pause("rule:B")
        assert p.paused
        p.resume("rule:A")
        assert p.paused, "still paused by rule:B"
        p.resume("rule:B")
        assert not p.paused
        p.resume(None)
        return "pause reasons ok"
    finally:
        p.stop()
        p.wait_stopped(2)


def test_inline_node_actions():
    import core.player as player_mod

    tmp = Path(tempfile.mkdtemp(prefix="kmg_inline_"))
    frame = make_frame()
    tpl = frame[60:91, 80:121].copy()
    tpl_path = tmp / "tpl.png"
    save_png(tpl, tpl_path)
    sub = tmp / "sub.json"
    ScriptDoc([{"type": "wait", "ms": 10}], sub).save()

    class FakeSource:
        origin = (0, 0)
        def __init__(self, img):
            self.img = img
        def grab(self, rect):
            return self.img.copy()
        def close(self):
            pass

    statuses = []
    orig = player_mod.make_source
    try:
        player_mod.make_source = lambda ev: FakeSource(frame)
        p = Player(TemplateMatcher(), lambda t, pr: statuses.append(t), lambda r: None)
        p.dry_run = True
        p.current_script = tmp / "main.json"
        ev = {"type": "monitor", "name": "m", "region": [0, 0, 200, 150],
              "template": str(tpl_path), "threshold": 0.9, "wait_mode": "once",
              "on_hit": [{"type": "script", "op": "run", "script": str(sub)}],
              "on_miss": [{"type": "script", "op": "stop"}]}
        assert p._exec_events([ev], None, top_level=True)
        assert any("运行子脚本" in s for s in statuses), statuses

        player_mod.make_source = lambda ev: FakeSource(np.zeros_like(frame))
        statuses.clear()
        ev2 = dict(ev)
        ev2["on_hit"] = []
        ev2["on_miss"] = []
        assert p._exec_events([ev2], None, top_level=True)
        assert any("未命中" in s for s in statuses), statuses
        return "inline hit/miss ok"
    finally:
        player_mod.make_source = orig


def test_background_rules():
    import core.rules as rules_mod
    from core.rules import BackgroundRuleEngine

    tmp = Path(tempfile.mkdtemp(prefix="kmg_rules_"))
    frame = make_frame()
    tpl = frame[60:91, 80:121].copy()
    tpl_path = tmp / "tpl.png"
    save_png(tpl, tpl_path)
    state = {"on": False}

    class FakeSource:
        origin = (0, 0)
        def grab(self, rect):
            return frame.copy() if state["on"] else np.zeros_like(frame)
        def close(self):
            pass

    cfg = {
        "multi_monitor": {
            "region": [0, 0, 200, 150],
            "capture_mode": "screen",
            "poll_ms": 30,
            "rules": [
                {"id": "A", "name": "A", "enabled": True, "resident": True, "priority": 90,
                 "template": str(tpl_path), "threshold": 0.9, "roi": [],
                 "stable_frames": 1, "cooldown_ms": 0,
                 "actions": [{"type": "script", "op": "run", "target": "script",
                              "path": str(tmp / "s1.json"), "busy": "switch"}],
                 "miss_actions": []},
                {"id": "B", "name": "B", "enabled": True, "resident": True, "priority": 80,
                 "template": str(tpl_path), "threshold": 0.9, "roi": [],
                 "stable_frames": 1, "cooldown_ms": 0,
                 "actions": [{"type": "script", "op": "pause", "target": "current"}],
                 "miss_actions": [{"type": "script", "op": "resume", "target": "current"}]},
            ],
            "links": [],
            "on_none_present": [{"type": "script", "op": "resume", "target": "current"}],
            "grace_ms": 80,
        },
    }
    actions = []
    orig = rules_mod.make_source
    rules_mod.make_source = lambda rule: FakeSource()
    eng = BackgroundRuleEngine(lambda: cfg, lambda rid, act: actions.append((rid, act)))
    try:
        eng.start()
        state["on"] = True
        end = time.time() + 3
        while time.time() < end:
            ops = {a.get("op") for _rid, a in actions}
            if "run" in ops and "pause" in ops:
                break
            time.sleep(0.03)
        ops = {a.get("op") for _rid, a in actions}
        assert "run" in ops and "pause" in ops, actions
        state["on"] = False
        end = time.time() + 3
        while time.time() < end:
            if any(a.get("op") == "resume" for _rid, a in actions):
                break
            time.sleep(0.03)
        assert any(a.get("op") == "resume" for _rid, a in actions), actions
        return "rule hit/miss actions ok"
    finally:
        eng.stop()
        eng.join(timeout=2)
        rules_mod.make_source = orig


def test_input_actions():
    from core.actions import action_summary, execute_input
    assert execute_input({"type": "click", "button": "left", "clicks": 2,
                          "hold_ms": 1, "interval_ms": 0, "position": "center"},
                         (10, 10), dry_run=True) == "ok"
    assert execute_input({"type": "wheel", "direction": "down", "amount": 2,
                          "interval_ms": 0}, (10, 10), dry_run=True) == "ok"
    assert execute_input({"type": "key", "keys": "ctrl+a", "hold_ms": 1},
                         dry_run=True) == "ok"
    assert execute_input({"type": "script", "op": "run"}, dry_run=True) == "script"
    assert "鼠标" in action_summary({"type": "click"})
    assert "键盘" in action_summary({"type": "key", "keys": "a"})
    return "click/wheel/key dry-run ok"


def test_window_geometry():
    from core.window_geometry import rect_visible_enough, center_on_primary, primary_work_area
    rects = [(0, 0, 1920, 1080), (1920, 0, 1280, 1024)]
    assert rect_visible_enough(100, 100, 1200, 860, rects)
    assert not rect_visible_enough(5000, 5000, 1200, 860, rects)
    assert rect_visible_enough(1800, 200, 1200, 860, rects)
    px, py, pw, ph = primary_work_area()
    x, y = center_on_primary(1200, 860)
    assert px <= x <= px + pw and py <= y <= py + ph
    return "visible/center ok"


def test_hotkey_conflicts():
    from core.hotkeys import normalize_hotkey, hotkey_conflicts
    assert normalize_hotkey(" f9 ") == "F9"
    dup = hotkey_conflicts([("启动/暂停", "F9"), ("停止", "f9"), ("录制", "F10")])
    assert list(dup) == ["F9"] and len(dup["F9"]) == 2
    assert hotkey_conflicts([("a", "F6"), ("b", "F7")]) == {}
    return "case-insensitive conflict ok"


def main():
    tests = [test_monitor_hit_and_anchor, test_monitor_timeout, test_monitor_click_modes,
             test_monitor_key_action, test_hotkey_parse,
             test_recorder_events, test_monitor_run_script_sync, test_keepalive_integrated,
             test_crop_match,
             test_preview_conversion,
             test_event_describe, test_wait_and_script_node, test_parsec_bundle,
             test_wheel_action, test_wheel_multi_action, test_keygrab_names,
             test_monitor_profile_wheel_roundtrip, test_window_capture,
             test_supervisor_edges, test_pause_reasons,
             test_inline_node_actions, test_background_rules, test_input_actions,
             test_window_geometry, test_hotkey_conflicts]
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




