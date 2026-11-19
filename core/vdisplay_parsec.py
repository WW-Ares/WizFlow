"""Parsec VDD 虚拟显示器：内置驱动安装 + 虚拟屏开关 + 心跳保活。

驱动来源：nomi-san/parsec-vdd v0.45.1（MIT），签名文件随程序打包在 assets/vdd。
安装命令与官方安装器一致（nefconw 创建 Root\\Parsec\\VDA 设备节点并绑定 mm.inf）。
只操作本模块自己 Add 出来的虚拟屏，不动 UU / 系统已有的显示器。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from config import BUNDLE_DIR, LOGS_DIR, ROOT
from .log import log
from .winapi import is_admin

GUID_STR = "{00b41627-04c4-429e-a26e-0265cf50c8fa}"
CLASS_GUID = "{4D36E968-E325-11CE-BFC1-08002BE10318}"
HWID = r"Root\Parsec\VDA"

IOCTL_ADD = 0x0022E004
IOCTL_REMOVE = 0x0022A008
IOCTL_UPDATE = 0x0022A00C

HEARTBEAT_INTERVAL = 0.05
INVALID_HANDLE = ctypes.c_void_p(-1).value
_IN_BYTES = 32
_OUT_BYTES = 32

CREATE_NO_WINDOW = 0x08000000
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_HIDE = 0


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class _DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [("cb", w.DWORD), ("DeviceName", w.WCHAR * 32),
                ("DeviceString", w.WCHAR * 128), ("StateFlags", w.DWORD),
                ("DeviceID", w.WCHAR * 128), ("DeviceKey", w.WCHAR * 128)]


class _SHELLEXECUTEINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("fMask", w.ULONG), ("hwnd", w.HWND),
                ("lpVerb", w.LPCWSTR), ("lpFile", w.LPCWSTR),
                ("lpParameters", w.LPCWSTR), ("lpDirectory", w.LPCWSTR),
                ("nShow", ctypes.c_int), ("hInstApp", w.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", w.LPCWSTR),
                ("hkeyClass", w.HKEY), ("dwHotKey", w.DWORD),
                ("hIcon", w.HANDLE), ("hProcess", w.HANDLE)]


_apis = None


def _load_apis():
    global _apis
    if _apis is not None:
        return _apis
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sa = ctypes.WinDLL("setupapi", use_last_error=True)
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)

    k32.CreateFileW.restype = w.HANDLE
    k32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                w.DWORD, w.DWORD, w.HANDLE]
    k32.DeviceIoControl.restype = w.BOOL
    k32.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, ctypes.c_void_p, w.DWORD,
                                    ctypes.c_void_p, w.DWORD,
                                    ctypes.POINTER(w.DWORD), ctypes.c_void_p]
    k32.CloseHandle.restype = w.BOOL
    k32.CloseHandle.argtypes = [w.HANDLE]

    sa.SetupDiGetClassDevsW.restype = w.HANDLE
    sa.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(_GUID), w.LPCWSTR, w.HWND, w.DWORD]
    sa.SetupDiEnumDeviceInterfaces.restype = w.BOOL
    sa.SetupDiEnumDeviceInterfaces.argtypes = [w.HANDLE, ctypes.c_void_p,
                                               ctypes.POINTER(_GUID), w.DWORD,
                                               ctypes.c_void_p]
    sa.SetupDiGetDeviceInterfaceDetailW.restype = w.BOOL
    sa.SetupDiGetDeviceInterfaceDetailW.argtypes = [w.HANDLE, ctypes.c_void_p,
                                                    ctypes.c_void_p, w.DWORD,
                                                    ctypes.POINTER(w.DWORD),
                                                    ctypes.c_void_p]
    sa.SetupDiDestroyDeviceInfoList.restype = w.BOOL
    sa.SetupDiDestroyDeviceInfoList.argtypes = [w.HANDLE]

    u32.EnumDisplayDevicesW.restype = w.BOOL
    u32.EnumDisplayDevicesW.argtypes = [w.LPCWSTR, w.DWORD,
                                        ctypes.POINTER(_DISPLAY_DEVICE), w.DWORD]

    ole.CLSIDFromString.restype = ctypes.c_long
    ole.CLSIDFromString.argtypes = [w.LPCWSTR, ctypes.POINTER(_GUID)]

    _apis = (k32, sa, u32, ole)
    return _apis


def _find_device_path():
    """返回 (设备接口路径, 错误)。未安装驱动时路径为 None。"""
    k32, sa, _u32, ole = _load_apis()
    guid = _GUID()
    if ole.CLSIDFromString(GUID_STR, ctypes.byref(guid)) != 0:
        return None, "GUID 解析失败"
    h = sa.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, 0x12)
    if not h or h == INVALID_HANDLE:
        return None, "SetupDiGetClassDevs 失败（错误码 %d）" % ctypes.get_last_error()
    try:
        idx = 0
        while True:
            itf = (ctypes.c_byte * 32)()
            ctypes.cast(itf, ctypes.POINTER(w.DWORD))[0] = 32
            if not sa.SetupDiEnumDeviceInterfaces(h, None, ctypes.byref(guid), idx, itf):
                break
            need = w.DWORD(0)
            sa.SetupDiGetDeviceInterfaceDetailW(h, itf, None, 0, ctypes.byref(need), None)
            if need.value:
                buf = (ctypes.c_byte * need.value)()
                ctypes.cast(buf, ctypes.POINTER(w.DWORD))[0] = 8
                if sa.SetupDiGetDeviceInterfaceDetailW(h, itf, buf, need.value,
                                                       ctypes.byref(need), None):
                    return ctypes.wstring_at(ctypes.addressof(buf) + 4), ""
            idx += 1
    finally:
        sa.SetupDiDestroyDeviceInfoList(h)
    return None, "Parsec 虚拟屏驱动未安装"


def _open_device():
    k32 = _load_apis()[0]
    path, err = _find_device_path()
    if not path:
        return None, err
    h = k32.CreateFileW(path, 0xC0000000, 3, None, 3, 0, None)
    if not h or h == INVALID_HANDLE:
        return None, "打开虚拟屏设备失败（错误码 %d）" % ctypes.get_last_error()
    return h, ""


def _ioctl(handle, code, payload=None):
    k32 = _load_apis()[0]
    ib = (ctypes.c_byte * _IN_BYTES)()
    if payload:
        for i, b in enumerate(payload[:_IN_BYTES]):
            ib[i] = b - 256 if b > 127 else b
    ob = (ctypes.c_byte * _OUT_BYTES)()
    ret = w.DWORD(0)
    ok = k32.DeviceIoControl(handle, code, ib, _IN_BYTES, ob, _OUT_BYTES,
                             ctypes.byref(ret), None)
    return bool(ok), ret.value, ctypes.string_at(ctypes.addressof(ob), _OUT_BYTES)


def _remove_payload(index: int) -> bytes:
    v = ((index & 0xFF) << 8) | ((index >> 8) & 0xFF)
    return bytes([v & 0xFF, (v >> 8) & 0xFF])


def active_displays():
    _k32, _sa, u32, _ole = _load_apis()
    out = []
    dev = _DISPLAY_DEVICE()
    dev.cb = ctypes.sizeof(_DISPLAY_DEVICE)
    i = 0
    while u32.EnumDisplayDevicesW(None, i, ctypes.byref(dev), 0):
        if "Parsec" in dev.DeviceString and (dev.StateFlags & 0x1):
            out.append((dev.DeviceName, int(dev.StateFlags)))
        dev = _DISPLAY_DEVICE()
        dev.cb = ctypes.sizeof(_DISPLAY_DEVICE)
        i += 1
    return out


def active_count() -> int:
    try:
        return len(active_displays())
    except Exception:
        return 0


# ---------------------------------------------------------------- 驱动文件
def driver_dir() -> Path:
    bundled = BUNDLE_DIR / "assets" / "vdd"
    if bundled.exists():
        return bundled
    return ROOT / "assets" / "vdd"


def driver_files_ok() -> bool:
    d = driver_dir()
    return all((d / p).exists() for p in ("nefconw.exe", "driver/mm.inf", "driver/mm.dll", "driver/mm.cat"))


def driver_installed() -> bool:
    path, _err = _find_device_path()
    return bool(path)


def _run_hidden(args: list[str], timeout: float = 120) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           encoding="mbcs", errors="replace",
                           creationflags=CREATE_NO_WINDOW)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()
    except Exception as exc:
        return -1, str(exc)


def _run_elevated(cmd_file: str, timeout: float = 180) -> tuple[int, str]:
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.restype = w.BOOL
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFO)]
    k32.WaitForSingleObject.restype = w.DWORD
    k32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    k32.GetExitCodeProcess.restype = w.BOOL
    k32.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    k32.CloseHandle.argtypes = [w.HANDLE]

    info = _SHELLEXECUTEINFO()
    info.cbSize = ctypes.sizeof(_SHELLEXECUTEINFO)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = "cmd.exe"
    info.lpParameters = '/c "%s"' % cmd_file
    info.lpDirectory = str(Path(cmd_file).parent)
    info.nShow = SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == 1223:
            return -2, "已取消管理员授权"
        return -1, "启动安装程序失败（错误码 %d）" % err
    try:
        k32.WaitForSingleObject(info.hProcess, int(timeout * 1000))
        code = w.DWORD(0)
        k32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value), ""
    finally:
        try:
            k32.CloseHandle(info.hProcess)
        except Exception:
            pass


def _run_driver_commands(commands: list[list[str]], tag: str) -> tuple[int, str]:
    """管理员时直接跑；普通权限时生成 cmd 脚本并 UAC 提权一次。"""
    if is_admin():
        out = []
        code = 0
        for args in commands:
            # 前面的 remove-device-node 在设备不存在时会返回非 0，不能因此中断
            code, text = _run_hidden(args)
            if text:
                out.append(text)
        return code, "\n".join(out)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="wizflow_vdd_"))
    cmd = work / ("%s.cmd" % tag)
    logf = work / ("%s.log" % tag)
    lines = ["@echo off", "setlocal"]
    for args in commands:
        quoted = " ".join('"%s"' % a if (" " in a or "\\" in a) else a for a in args)
        lines.append(quoted)
    # 只取最后一步（install-driver / uninstall-driver）的返回码
    lines += ["if errorlevel 1 (echo FAIL>\"%s\" & exit /b 1)" % logf,
              "echo OK>\"%s\"" % logf, "exit /b 0"]
    cmd.write_text("\r\n".join(lines) + "\r\n", encoding="mbcs", errors="replace")
    code, err = _run_elevated(str(cmd))
    text = ""
    if logf.exists():
        try:
            text = logf.read_text(encoding="mbcs", errors="replace").strip()
        except Exception:
            text = ""
    if code == 0 and text == "OK":
        return 0, "OK"
    if code == -2:
        return code, err
    return code if code != 0 else -1, err or ("驱动安装失败（见 %s）" % work)


def install_driver() -> str:
    """安装并创建设备节点。返回空字符串表示成功。"""
    if not driver_files_ok():
        return "内置驱动文件缺失：%s" % driver_dir()
    d = driver_dir()
    nef = str(d / "nefconw.exe")
    inf = str(d / "driver" / "mm.inf")
    cmds = [
        [nef, "--remove-device-node", "--hardware-id", HWID, "--class-guid", CLASS_GUID],
        [nef, "--create-device-node", "--class-name", "Display", "--class-guid", CLASS_GUID,
         "--hardware-id", HWID],
        [nef, "--install-driver", "--inf-path", inf],
    ]
    code, text = _run_driver_commands(cmds, "install")
    if code == 0 or driver_installed() or active_count() > 0:
        log("Parsec VDD 驱动安装完成 code=%s" % code)
        return ""
    log("Parsec VDD 驱动安装失败 code=%s %s" % (code, text), "WARN")
    return text or ("安装失败（code=%s）" % code)


def remove_driver() -> str:
    if not driver_files_ok():
        return "内置驱动文件缺失"
    d = driver_dir()
    nef = str(d / "nefconw.exe")
    inf = str(d / "driver" / "mm.inf")
    cmds = [
        [nef, "--remove-device-node", "--hardware-id", HWID, "--class-guid", CLASS_GUID],
        [nef, "--uninstall-driver", "--inf-path", inf],
    ]
    code, text = _run_driver_commands(cmds, "uninstall")
    if code == 0:
        log("Parsec VDD 驱动已卸载")
        return ""
    return text or ("卸载失败（code=%s）" % code)


# ---------------------------------------------------------------- 控制器
class VirtualDisplay:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop: threading.Event | None = None
        self._handle = None
        self._index: int | None = None
        self.enabled = False
        self.last_error = ""
        self.added_at = 0.0

    def _close_handle_locked(self):
        h = self._handle
        self._handle = None
        if h:
            try:
                _load_apis()[0].CloseHandle(h)
            except Exception:
                pass
        return h

    def _add_locked(self, attempts: int = 10, delay: float = 0.5) -> str:
        err = ""
        open_fail = 0
        for _ in range(max(1, attempts)):
            self._close_handle_locked()
            h, err = _open_device()
            if not h:
                open_fail += 1
                if open_fail >= 2:
                    return err
                time.sleep(delay)
                continue
            open_fail = 0
            self._handle = h
            ok, _ret, out = _ioctl(h, IOCTL_ADD)
            if ok:
                self._index = int.from_bytes(out[:4], "little")
                self.added_at = time.time()
                return ""
            err = "添加虚拟屏失败（错误码 %d）" % ctypes.get_last_error()
            self._close_handle_locked()
            time.sleep(delay)
        return err or "添加虚拟屏失败"

    def _heartbeat_loop(self):
        stop = self._stop
        check = 0
        while stop is not None and not stop.is_set():
            try:
                with self._lock:
                    if self._handle is None and self.enabled:
                        self._add_locked()
                    h = self._handle
                if h is None:
                    time.sleep(1.0)
                    continue
                ok, _ret, _out = _ioctl(h, IOCTL_UPDATE)
                if not ok:
                    with self._lock:
                        if self._handle == h:
                            self._close_handle_locked()
                    time.sleep(0.2)
                    continue
                check += 1
                if check >= 20:
                    check = 0
                    if active_count() == 0:
                        with self._lock:
                            self._add_locked()
            except Exception as exc:
                self.last_error = str(exc)
                time.sleep(0.5)
            stop.wait(HEARTBEAT_INTERVAL)

    def ensure_on(self, wait_ms: int = 1500) -> str:
        with self._lock:
            self.enabled = True
            self.last_error = ""
            if self._stop is None:
                self._stop = threading.Event()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._heartbeat_loop,
                                                name="wizflow-vdd", daemon=True)
                self._thread.start()
            if self._handle is None:
                err = self._add_locked()
                if err:
                    self.last_error = err
                    self.enabled = False
                    return err
        deadline = time.time() + max(0.3, wait_ms / 1000.0)
        while time.time() < deadline:
            if active_count() > 0:
                return ""
            time.sleep(0.05)
        return "" if active_count() > 0 else (self.last_error or "虚拟屏未激活")

    def ensure_off(self, wait_ms: int = 2000) -> None:
        with self._lock:
            self.enabled = False
            stop = self._stop
            if stop is not None:
                stop.set()
            thread = self._thread
            self._thread = None
            self._stop = None
            h = self._handle
            idx = self._index
            self._handle = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        if h:
            try:
                _load_apis()[0].CloseHandle(h)
            except Exception:
                pass
        deadline = time.time() + max(0.0, wait_ms / 1000.0)
        while time.time() < deadline and active_count() > 0:
            time.sleep(0.1)
        if idx is not None and active_count() > 0:
            h2, _err = _open_device()
            if h2:
                try:
                    _ioctl(h2, IOCTL_REMOVE, _remove_payload(idx))
                finally:
                    try:
                        _load_apis()[0].CloseHandle(h2)
                    except Exception:
                        pass
                time.sleep(0.2)

    def status(self) -> dict:
        return {"enabled": bool(self.enabled), "active": active_count(),
                "installed": driver_installed(), "error": self.last_error}


virtual = VirtualDisplay()


def ensure_on(wait_ms: int = 1500) -> str:
    return virtual.ensure_on(wait_ms)


def ensure_off(wait_ms: int = 2000) -> None:
    virtual.ensure_off(wait_ms)


def status() -> dict:
    return virtual.status()
