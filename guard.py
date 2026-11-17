# -*- coding: utf-8 -*-
"""guard.py - 鼠标守卫 + 停止键。

1) WH_MOUSE_LL：租约期内吞掉【物理】鼠标事件（移动/各键按下与抬起/滚轮），
   放行 SendInput 注入事件。按下被吞时抬起也吞，否则右键菜单照样会弹出。
2) WH_KEYBOARD_LL：租约期内拦截 Ctrl+C 作为停止键——立刻释放鼠标、还原并置顶
   终端，并写 guard.stop 标记。pc.py 见到标记就不再执行键鼠动作；
   停止键本身不向终端注入 Ctrl+C，所以不会打断 Claude Code 当前任务。
   不在租约期内时 Ctrl+C 完全透传（正常复制不受影响）。
3) 租约失效时自动还原终端窗口；进程退出 = 钩子卸载 = 鼠标立刻恢复。

终端窗口句柄由 pc.py 写入 %TEMP%\\claude-eyes\\terminal.json。
"""
import ctypes
import json
import os
import sys
import tempfile
import time
from ctypes import wintypes

BASE = os.path.join(tempfile.gettempdir(), "claude-eyes")
LEASE = os.path.join(BASE, "guard.lease")
STOP_FILE = os.path.join(BASE, "guard.stop")
PIDFILE = os.path.join(BASE, "guard.pid")
TERM_FILE = os.path.join(BASE, "terminal.json")
MUTEX_NAME = "Local\\claude-eyes-mouse-guard"

WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13
WM_TIMER = 0x0113
LLMHF_INJECTED = 0x0001
ERROR_ALREADY_EXISTS = 183
TICK_MS = 200
IDLE_EXIT_SEC = 120
DOWN_EVENTS = {0x0201, 0x0204, 0x0207, 0x020B}                       # 左/右/中/X 键按下
UP_TO_DOWN = {0x0202: 0x0201, 0x0205: 0x0204, 0x0208: 0x0207, 0x020C: 0x020B}
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
VK_CONTROL, VK_C = 0x11, 0x43
SW_RESTORE = 9
KEYEVENTF_KEYUP = 0x0002

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


HOOKPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = wintypes.LPARAM
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = wintypes.LPARAM
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE

ACTIVE_UNTIL = 0.0
LAST_ACTIVE = time.time()
WAS_ACTIVE = False
PENDING_RESTORE = 0.0
CTRL_C_HELD = False
SWALLOWED_DOWN = {}


def read_lease():
    try:
        with open(LEASE, "r") as f:
            return float(f.read().strip() or 0)
    except Exception:
        return 0.0


def _key(vk, up=False):
    return INPUT(type=1, u=_INPUTunion(ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)))


def _send(*events):
    arr = (INPUT * len(events))(*events)
    user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def _tap_alt():
    _send(_key(0x12), _key(0x12, True))


def restore_terminal():
    """把 Claude Code 的终端窗口还原并置顶。"""
    try:
        with open(TERM_FILE) as f:
            t = json.load(f)
        hwnd = int(t.get("hwnd") or 0)
        pid = int(t.get("pid") or 0)
    except Exception:
        return False
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    owner = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    if pid and int(owner.value) != pid:
        return False
    user32.ShowWindow(hwnd, SW_RESTORE)
    time.sleep(0.06)
    _tap_alt()                       # 解除跨进程 SetForegroundWindow 限制
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.08)
    return True


def trigger_stop():
    """停止键：立刻释放鼠标、写停止标记、安排还原终端。不打断 Claude Code。"""
    global ACTIVE_UNTIL, PENDING_RESTORE
    ACTIVE_UNTIL = 0.0
    try:
        with open(LEASE, "w") as f:
            f.write("0")
    except OSError:
        pass
    try:
        with open(STOP_FILE, "w") as f:
            f.write("%.0f" % time.time())
    except OSError:
        pass
    PENDING_RESTORE = time.time() + 0.03


@HOOKPROC
def mouse_proc(nCode, wParam, lParam):
    try:
        if nCode == 0 and time.time() < ACTIVE_UNTIL:
            info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if not (info.flags & LLMHF_INJECTED):
                if wParam in DOWN_EVENTS:
                    SWALLOWED_DOWN[wParam] = True
                    return 1
                down = UP_TO_DOWN.get(wParam)
                if down is not None:
                    if SWALLOWED_DOWN.pop(down, False):
                        return 1        # 按下被吞了，抬起也吞，否则右键菜单照样弹
                else:
                    return 1            # 滚轮
    except Exception:
        pass
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


@HOOKPROC
def key_proc(nCode, wParam, lParam):
    global CTRL_C_HELD
    try:
        if nCode == 0 and time.time() < ACTIVE_UNTIL:
            info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if info.vkCode == VK_C:
                if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if CTRL_C_HELD:
                        return 1
                    if user32.GetAsyncKeyState(VK_CONTROL) & 0x8000:
                        CTRL_C_HELD = True
                        trigger_stop()
                        return 1
                elif wParam in (WM_KEYUP, WM_SYSKEYUP) and CTRL_C_HELD:
                    CTRL_C_HELD = False
                    return 1
    except Exception:
        pass
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


def tick():
    global ACTIVE_UNTIL, LAST_ACTIVE, WAS_ACTIVE, PENDING_RESTORE
    ACTIVE_UNTIL = read_lease()
    now = time.time()
    if ACTIVE_UNTIL > now:
        LAST_ACTIVE = now

    if PENDING_RESTORE:
        if now >= PENDING_RESTORE:
            PENDING_RESTORE = 0.0
            restore_terminal()
            WAS_ACTIVE = False
    elif WAS_ACTIVE and ACTIVE_UNTIL <= now:
        restore_terminal()                    # 租约到期：终端自动回来
        WAS_ACTIVE = False

    if ACTIVE_UNTIL > now:
        WAS_ACTIVE = True

    if not PENDING_RESTORE and ACTIVE_UNTIL <= now and now - LAST_ACTIVE > IDLE_EXIT_SEC:
        user32.PostQuitMessage(0)


def main():
    global ACTIVE_UNTIL, LAST_ACTIVE
    os.makedirs(BASE, exist_ok=True)
    kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return 0

    ACTIVE_UNTIL = read_lease()
    LAST_ACTIVE = time.time()
    hmod = kernel32.GetModuleHandleW(None)
    hm = user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_proc, hmod, 0)
    hk = user32.SetWindowsHookExW(WH_KEYBOARD_LL, key_proc, hmod, 0)
    if not hm or not hk:
        if hm:
            user32.UnhookWindowsHookEx(hm)
        if hk:
            user32.UnhookWindowsHookEx(hk)
        return 2
    try:
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))
        user32.SetTimer(None, 1, TICK_MS, None)
        msg = wintypes.MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r <= 0:
                break
            if msg.message == WM_TIMER:
                tick()
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        user32.UnhookWindowsHookEx(hm)
        user32.UnhookWindowsHookEx(hk)
        if time.time() < read_lease():
            restore_terminal()                # 被强行退出且仍在接管期：还给用户
        try:
            os.remove(PIDFILE)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
