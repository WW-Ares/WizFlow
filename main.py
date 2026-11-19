"""WizFlow 启动入口。

    python main.py                 启动界面
    python main.py --selftest      核心自检（不打开界面）
    python main.py --run x.json    直接回放脚本（Ctrl+C 停止）
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config  # noqa: E402
from core.winapi import set_dpi_aware  # noqa: E402

set_dpi_aware()


def _selftest() -> int:
    import tempfile
    from pathlib import Path

    import cv2
    import numpy as np

    from config import Config, LOGS_DIR
    from core.player import Player
    from core.script import ScriptDoc
    from core.screen import TemplateMatcher, grab, load_image, save_png
    from core.vdisplay import status as vd_status

    lines = []
    ok_all = True

    def check(name, fn):
        nonlocal ok_all
        try:
            detail = fn()
            lines.append("PASS  %-22s %s" % (name, detail or ""))
        except Exception as exc:
            ok_all = False
            lines.append("FAIL  %-22s %s" % (name, exc))

    tmp = Path(tempfile.mkdtemp(prefix="kmgpro_"))

    def t_config():
        c = Config(tmp / "config.json")
        c.set("hotkeys.stop", "F9")
        c.save()
        c2 = Config(tmp / "config.json")
        assert c2.get("hotkeys.stop") == "F9"
        return str(c2.get("hotkeys.stop"))

    def t_script():
        events = [
            {"type": "event", "event_type": "EM", "delay": 10,
             "action_type": "mouse left down", "action": [100, 200]},
            {"type": "monitor", "name": "m1", "region": [0, 0, 100, 100],
             "template": "icons/x.png", "threshold": 0.8},
        ]
        p = tmp / "s.json"
        ScriptDoc(events, p).save()
        doc = ScriptDoc.load(p)
        assert len(doc.events) == 2 and doc.events[1]["type"] == "monitor"
        return "%d events" % len(doc.events)

    def t_legacy():
        p = tmp / "legacy.txt"
        p.write_text('[[300, "EM", "mouse right down", [0.05, 0.18]]]', encoding="utf-8")
        doc = ScriptDoc.load(p)
        assert len(doc.events) == 1 and doc.events[0]["action_type"] == "mouse right down"
        return "legacy list ok"

    def t_match():
        frame = np.zeros((160, 240, 3), np.uint8)
        cv2.rectangle(frame, (50, 40), (92, 72), (0, 200, 255), -1)
        cv2.circle(frame, (70, 55), 8, (255, 40, 40), -1)
        tpl = frame[40:73, 50:93].copy()
        tp = tmp / "tpl.png"
        save_png(tpl, tp)
        m = TemplateMatcher()
        res = m.find(frame, str(tp), 0.85, True)
        assert res.score >= 0.9, "score=%.3f" % res.score
        assert abs(res.center[0] - 71) <= 4 and abs(res.center[1] - 56) <= 4, res.center
        return "score=%.3f center=%s" % (res.score, res.center)

    def t_grab():
        img = grab((0, 0, 160, 100))
        assert img.shape[0] == 100 and img.shape[1] == 160
        p = tmp / "grab.png"
        save_png(img, p)
        assert load_image(p) is not None
        return "shape=%s" % (img.shape,)

    def t_player():
        done = []
        p = Player(TemplateMatcher(), lambda t, pr: None, lambda r: done.append(r))
        p.dry_run = True
        evs = [
            {"type": "event", "event_type": "EM", "delay": 5,
             "action_type": "mouse move", "action": [10, 10]},
            {"type": "event", "event_type": "EK", "delay": 5,
             "action_type": "key down", "action": [65, "a", 0]},
            {"type": "event", "event_type": "EK", "delay": 5,
             "action_type": "key up", "action": [65, "a", 0]},
        ]
        assert p.start(evs, 2, 4.0, script_dir=tmp)
        deadline = time.time() + 5
        while p.running and time.time() < deadline:
            time.sleep(0.02)
        assert done and done[-1] == "finished", done
        return "finished"

    def t_vdisplay():
        st = vd_status()
        return "mode=%s enabled=%s nudges=%s" % (st.get("mode"), st.get("enabled"), st.get("nudges"))

    def t_describe():
        from core.script import describe_event
        d = describe_event({"type": "event", "event_type": "EM",
                            "action_type": "mouse left down", "action": [10, 20]})
        assert d["kind"] == "鼠标" and d["action"] == "左键按下", d
        return "%s/%s" % (d["kind"], d["action"])

    def t_parsec():
        from core import vdisplay_parsec as vp
        assert vp.driver_files_ok(), vp.driver_dir()
        st = vp.status()
        return "installed=%s active=%s" % (st.get("installed"), st.get("active"))

    def t_capture():
        import tkinter as tk
        from core.capture import list_windows, make_source
        from core.screen import TemplateMatcher, save_png
        import numpy as np

        root = tk.Tk()
        root.title("WizFlowSelfTestCap")
        root.geometry("600x400+140+140")
        cv = tk.Canvas(root, bg="#101820", highlightthickness=0)
        cv.pack(fill="both", expand=True)
        cv.create_rectangle(40, 40, 100, 80, fill="#00ff00", outline="")
        cv.create_rectangle(45, 45, 55, 55, fill="#ff0000", outline="")
        try:
            for _ in range(20):
                root.update()
                time.sleep(0.02)
            wins = [w for w in list_windows() if w.title == "WizFlowSelfTestCap"]
            assert wins, "self-test window not found"
            w = wins[0]
            tpl = np.zeros((40, 60, 3), np.uint8)
            tpl[:, :] = (0, 255, 0)
            tpl[5:15, 5:15] = (0, 0, 255)
            tp = tmp / "cap_tpl.png"
            save_png(tpl, tp)
            out = []
            for mode in ("window", "window_gdi"):
                src = make_source({"capture_mode": mode, "window_hwnd": w.hwnd})
                try:
                    frame = src.grab((0, 0, min(600, w.width), min(400, w.height)))
                    res = TemplateMatcher().find(frame, str(tp), 0.9, False)
                    assert abs(res.center[0] - 70) <= 3 and abs(res.center[1] - 60) <= 3, res.center
                    out.append(mode)
                finally:
                    src.close()
            return "windows=%d modes=%s" % (len(list_windows()), ",".join(out))
        finally:
            root.destroy()

    def t_supervisor():
        import core.supervisor as sup_mod
        from core.supervisor import ScriptSupervisor
        c = Config(tmp / "sup.json")
        assert c.get("script_supervisor.appear") == "none"
        c.set("script_supervisor.appear", "pause")
        c.set("script_supervisor.disappear", "resume")
        c.save()
        assert Config(tmp / "sup.json").get("script_supervisor.disappear") == "resume"

        frame = np.zeros((120, 160, 3), np.uint8)
        cv2.rectangle(frame, (40, 30), (80, 70), (0, 200, 255), -1)
        cv2.circle(frame, (60, 50), 8, (255, 40, 40), -1)
        tpl = frame[30:71, 40:81].copy()
        tp = tmp / "sup_tpl.png"
        save_png(tpl, tp)
        state = {"on": False}

        class FakeSource:
            origin = (0, 0)

            def grab(self, region):
                return frame.copy() if state["on"] else np.zeros_like(frame)

            def close(self):
                pass

        orig = sup_mod.make_source
        sup_mod.make_source = lambda cfg: FakeSource()
        actions = []
        cfg = {"enabled": True, "template": str(tp), "threshold": 0.9,
               "poll_ms": 10, "stable_frames": 2, "cooldown_ms": 0,
               "appear": "pause", "disappear": "resume", "run_script": "",
               "region": [0, 0, 160, 120], "capture_mode": "screen",
               "window_hwnd": 0, "window_title": "", "window_class": "",
               "window_process": ""}
        s = ScriptSupervisor(lambda: dict(cfg), lambda a, t: actions.append(a))
        try:
            s.start()
            state["on"] = True
            end = time.time() + 3
            while time.time() < end and not s.status()["present"]:
                time.sleep(0.02)
            state["on"] = False
            end = time.time() + 3
            while time.time() < end and s.status()["present"]:
                time.sleep(0.02)
            time.sleep(0.1)
            assert actions[:2] == ["pause", "resume"], actions
            return "appear/disappear %s" % actions[:2]
        finally:
            s.stop()
            s.join(timeout=2)
            sup_mod.make_source = orig

    check("config", t_config)
    check("script-roundtrip", t_script)
    check("script-legacy", t_legacy)
    check("template-match", t_match)
    check("screen-grab", t_grab)
    check("player-dryrun", t_player)
    check("vdisplay", t_vdisplay)
    check("event-describe", t_describe)
    check("parsec-bundle", t_parsec)
    check("supervisor", t_supervisor)
    check("window-capture", t_capture)

    text = "\n".join(lines)
    print(text)
    try:
        (LOGS_DIR / "selftest.txt").write_text(text, encoding="utf-8")
    except Exception:
        pass
    return 0 if ok_all else 1


def _run_script(path: str, loops: int) -> int:
    from pathlib import Path
    from core.player import Player
    from core.script import ScriptDoc
    from core.screen import TemplateMatcher

    doc = ScriptDoc.load(path)
    p = Player(TemplateMatcher(),
               lambda t, pr: print("[%s] %s" % (time.strftime("%H:%M:%S"), t)),
               lambda r: print("finished:", r))
    p.start(doc.events, loops, 1.0, script_dir=Path(path).parent)
    try:
        while p.running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        p.stop()
        p.shutdown()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--run", type=str, default="")
    parser.add_argument("--loops", type=int, default=1)
    args, _unknown = parser.parse_known_args()

    if args.selftest:
        return _selftest()
    if args.run:
        return _run_script(args.run, args.loops)

    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WizFlow.Desktop")
    except Exception:
        pass
    try:
        import customtkinter as ctk
        ctk.deactivate_automatic_dpi_awareness()
        cfg = Config()
        scale = float(cfg.get("ui_scale", 1.15) or 1.15)
        scale = max(0.85, min(1.6, scale))
        os.environ["WIZFLOW_UI_SCALE"] = str(scale)
        ctk.set_widget_scaling(1.0 + (scale - 1.0) * 0.5)
    except Exception:
        pass
    from app.main_window import App
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "crash.log"),
                  "a", encoding="utf-8") as fh:
            fh.write(traceback.format_exc() + "\n")
        raise

