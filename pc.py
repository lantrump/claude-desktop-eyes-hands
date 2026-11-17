# -*- coding: utf-8 -*-
"""pc.py - 桌面手眼 CLI。

眼睛：GDI 抓屏（全屏/区域/窗口）保存为 PNG，供多模态模型查看。
手：直接 import 语音助手项目里已验证的 desktop_ctl.py（SendInput 动作层），
    不复制一份，也不常驻。每个子命令启动进程、执行、退出。

用法：
  python pc.py ping
  python pc.py shot [--out PATH] [--region x,y,w,h] [--window HWND] [--scale N]
  python pc.py windows
  python pc.py foreground
  python pc.py pos
  python pc.py activate 计算器
  python pc.py focus HWND [--pid PID]
  python pc.py move X Y
  python pc.py click X Y [--button right] [--double] [--mods ctrl shift]
  python pc.py drag X1 Y1 X2 Y2
  python pc.py scroll DY [DX]
  python pc.py key ctrl+s
  python pc.py type "文本" [--mode paste|line] [--clear]
  python pc.py wait 500
  python pc.py home
  python pc.py batch '[["click","800","450"],["wait","300"]]'

输出：stdout 一行 JSON；失败也输出 {"ok": false, ...}。
退出码：0 成功，1 一般错误，2 目标窗口是高完整性（管理员）窗口。
"""
import argparse
import ctypes
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from ctypes import wintypes

# 原地改编码，不新建 wrapper：desktop_ctl 导入时还会包一层，
# 两层 TextIOWrapper 叠同一个 buffer，先死的那层会把流关掉。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DSH_HOME = os.environ.get("DSH_VOICE_HOME", r"D:\dsh工作区\语音屏幕助手")
sys.path.insert(0, DSH_HOME)
try:
    import desktop_ctl as dc          # 导入时已完成 DPI 感知设置
except Exception as exc:
    print(json.dumps({"ok": False,
                      "error": "无法加载 %s\\desktop_ctl.py: %s" % (DSH_HOME, exc)},
                     ensure_ascii=False))
    sys.exit(1)

# ---------------- GDI 抓屏 ----------------
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32 = dc.user32

user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.GetDIBits.restype = ctypes.c_int

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def grab(x, y, w, h):
    """截虚拟屏幕上 (x,y) 起 w*h 的区域，返回 RGBA bytearray。坐标是物理像素。"""
    if w <= 0 or h <= 0:
        raise ValueError("截图尺寸无效: %sx%s" % (w, h))
    hdc = user32.GetDC(None)
    if not hdc:
        raise OSError("GetDC 失败")
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        if not gdi32.BitBlt(mdc, 0, 0, w, h, hdc, x, y, SRCCOPY | CAPTUREBLT):
            raise OSError("BitBlt 失败")
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(bi)
        bi.biWidth = w
        bi.biHeight = -h                  # 负数 = 自上而下，省一次翻转
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0
        buf = (ctypes.c_ubyte * (w * h * 4))()
        if not gdi32.GetDIBits(mdc, bmp, 0, h, ctypes.cast(buf, ctypes.c_void_p),
                               ctypes.byref(bi), DIB_RGB_COLORS):
            raise OSError("GetDIBits 失败")
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(None, hdc)
    raw = bytes(buf)                      # GDI 给的是 BGRA，转成 PNG 要的 RGBA
    rgba = bytearray(len(raw))
    rgba[0::4] = raw[2::4]
    rgba[1::4] = raw[1::4]
    rgba[2::4] = raw[0::4]
    rgba[3::4] = b"\xff" * (w * h)        # GDI 的 alpha 常为 0，必须补成不透明
    return rgba


def _png_chunk(kind, data):
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def write_png(path, rgba, w, h):
    stride = w * 4
    raw = b"".join(b"\x00" + bytes(rgba[y * stride:(y + 1) * stride]) for y in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)   # 8bit RGBA
    png = (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr)
           + _png_chunk(b"IDAT", zlib.compress(raw, 6)) + _png_chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def scale_nearest(rgba, w, h, s):
    """最近邻放大，只建议对裁出来的小图用（纯 Python，整屏放大很慢）。"""
    if s <= 1:
        return rgba, w, h
    out = bytearray()
    for y in range(h):
        row = rgba[y * w * 4:(y + 1) * w * 4]
        wide = b"".join(bytes(row[i:i + 4]) * s for i in range(0, len(row), 4))
        out += wide * s
    return out, w * s, h * s


def parse_region(text):
    parts = [int(p) for p in str(text).split(",")]
    if len(parts) != 4:
        raise ValueError("--region 需要 x,y,w,h，收到: %s" % text)
    return parts


def default_out():
    folder = os.path.join(tempfile.gettempdir(), "claude-eyes")
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (int(time.time() * 1000) % 1000)
    return os.path.join(folder, "shot-%s.png" % stamp)


# ---------------- 鼠标守卫 ----------------
# 动作命令执行前自动武装：守卫进程吞掉物理鼠标事件、放行我们的注入事件，
# 租约默认 20 秒，我每动一次就续期，停下 20 秒后自动恢复。
GUARD_DIR = os.path.join(tempfile.gettempdir(), "claude-eyes")
GUARD_LEASE = os.path.join(GUARD_DIR, "guard.lease")
GUARD_STOP = os.path.join(GUARD_DIR, "guard.stop")
GUARD_PID = os.path.join(GUARD_DIR, "guard.pid")
GUARD_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guard.py")
TERMINAL_FILE = os.path.join(GUARD_DIR, "terminal.json")
GUARD_TTL = 20.0
ACTION_CMDS = {"move", "click", "drag", "scroll", "key", "type", "activate", "focus"}
TERMINAL_CLASSES = {"CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass", "mintty", "PuTTY"}
SW_MINIMIZE = 6

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.CloseHandle.argtypes = [wintypes.HANDLE]


def _pid_alive(pid):
    if not pid:
        return False
    h = _k32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    _k32.CloseHandle(h)
    return True


def _guard_pid():
    try:
        with open(GUARD_PID) as f:
            return int(f.read().strip())
    except Exception:
        return 0


def _guard_running():
    return _pid_alive(_guard_pid())


def _spawn_guard():
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    flags = 0x00000008 | 0x08000000        # DETACHED_PROCESS | CREATE_NO_WINDOW
    subprocess.Popen([exe, GUARD_SCRIPT], cwd=os.path.dirname(GUARD_SCRIPT),
                     creationflags=flags, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)
    for _ in range(20):
        if _guard_running():
            return True
        time.sleep(0.05)
    return False


def _write_lease(seconds):
    os.makedirs(GUARD_DIR, exist_ok=True)
    until = time.time() + max(0.0, float(seconds))
    with open(GUARD_LEASE, "w") as f:
        f.write("%.3f" % until)
    return until


def _find_terminal():
    """找 Claude Code 所在的终端窗口：前台优先，否则按 Z 序找终端类窗口。"""
    fg = dc.foreground_info()
    if fg.get("hwnd") and fg.get("class") in TERMINAL_CLASSES:
        return fg
    for w in dc.list_visible_windows():
        if w.get("class") in TERMINAL_CLASSES:
            return w
    return None


def _hide_terminal():
    """把终端最小化，让截图能看到整块屏幕；守卫到期或 Ctrl+C 时再弹回来。"""
    t = _find_terminal()
    if not t:
        return None
    hwnd, pid = int(t["hwnd"]), int(t.get("pid") or 0)
    try:
        with open(TERMINAL_FILE, "w") as f:
            json.dump({"hwnd": hwnd, "pid": pid}, f)
    except OSError:
        pass
    if not user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_MINIMIZE)
    return {"hwnd": hwnd, "pid": pid}


def _arm(ttl=GUARD_TTL):
    if not _guard_running() and not _spawn_guard():
        return False
    _write_lease(ttl)
    _hide_terminal()
    return True


def _stop_flag():
    return os.path.exists(GUARD_STOP)


def _clear_stop():
    try:
        os.remove(GUARD_STOP)
    except OSError:
        pass


def _guard_state():
    try:
        with open(GUARD_LEASE) as f:
            until = float(f.read().strip() or 0)
    except Exception:
        until = 0.0
    now = time.time()
    return {"ok": True, "running": _guard_running(), "active": until > now,
            "active_until": round(until, 3), "remaining_ms": max(0, int((until - now) * 1000)),
            "stopped": _stop_flag(), "pid": _guard_pid()}


def cmd_guard(a):
    if a.action == "on":
        _clear_stop()
        ok = _arm(a.ttl if a.ttl is not None else 60.0)
        st = _guard_state()
        st["ok"] = ok
        if not ok:
            st["error"] = "守卫进程启动失败"
        return st
    if a.action == "off":
        _write_lease(0.0)
        return _guard_state()
    return _guard_state()


def cmd_home(a):
    """交还鼠标并把终端弹回前台，收工用。"""
    _write_lease(0.0)
    _clear_stop()
    try:
        with open(TERMINAL_FILE) as f:
            t = json.load(f)
    except Exception:
        return {"ok": True, "released": True, "restored": False, "reason": "no-terminal"}
    res = dc.focus_window(int(t.get("hwnd") or 0), int(t.get("pid") or 0))
    return {"ok": True, "released": True, "restored": bool(res.get("ok")), "focus": res}


def cmd_batch(a):
    """一次进程内连跑多条命令，省掉每步 ~0.14s 的启动开销和往返。"""
    try:
        steps = json.loads(a.steps)
    except ValueError as e:
        raise ValueError("steps 不是合法 JSON: %s" % e)
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps 需要非空数组")
    parser = build_parser()
    results = []
    for i, argv in enumerate(steps):
        if not isinstance(argv, list) or not argv:
            raise ValueError("第 %d 步应是非空字符串数组" % (i + 1))
        step = parser.parse_args([str(x) for x in argv])
        if step.cmd in ("batch", "guard", "home"):
            raise ValueError("batch 里不能嵌套 %s" % step.cmd)
        res = run(step)
        res.setdefault("ok", True)
        results.append(res)
        if not res.get("ok"):
            break
    return {"ok": True, "steps": len(results), "results": results}


def cmd_shot(a):
    if a.window:
        hwnd = int(a.window)
        if not user32.IsWindow(hwnd):
            raise ValueError("窗口句柄无效: %s" % hwnd)
        if user32.IsIconic(hwnd):
            raise ValueError("窗口已最小化，先 activate 再截")
        rect = dc.window_rect(hwnd)
        if not rect:
            raise ValueError("拿不到窗口位置: %s" % hwnd)
        x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
    elif a.region:
        x, y, w, h = parse_region(a.region)
    else:
        scr = dc.screen_info()["virtual"]
        x, y, w, h = scr["x"], scr["y"], scr["w"], scr["h"]
    rgba, out_w, out_h = scale_nearest(grab(x, y, w, h), w, h, max(1, int(a.scale)))
    path = os.path.abspath(a.out or default_out())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_png(path, rgba, out_w, out_h)
    return {"ok": True, "path": path, "x": x, "y": y, "w": w, "h": h,
            "scale": max(1, int(a.scale)), "outW": out_w, "outH": out_h,
            "foreground": dc.foreground_info(),
            "cursor": dict(zip(("x", "y"), dc.cursor_pos())),
            "screen": dc.screen_info()}


def cmd_type(a):
    text = a.text
    if a.mode == "line":
        res = dc.type_line_by_line(text, a.clear)
        return {"ok": True, "chars": res["chars"], "lines": res["lines"], "mode": "line"}
    if a.clear:
        dc.send_combo("ctrl+a")
        time.sleep(0.06)
    if text:
        dc.set_clipboard_text(text)       # 注意：会覆盖用户当前剪贴板
        time.sleep(0.05)
        dc.send_combo("ctrl+v")
    return {"ok": True, "chars": len(text), "mode": "paste"}


def run(a):
    if a.cmd in ACTION_CMDS:
        if _stop_flag():
            return {"ok": False, "code": "stopped",
                    "error": "用户按了停止键，操控已关闭；确认后执行 guard on 恢复"}
        _arm()
    if a.cmd == "guard":
        return cmd_guard(a)
    if a.cmd == "home":
        return cmd_home(a)
    if a.cmd == "batch":
        return cmd_batch(a)
    if a.cmd == "ping":
        return {"ok": True, "pid": os.getpid(), "dpi_aware": dc.DPI_MODE,
                "screen": dc.screen_info(), "dsh_home": DSH_HOME,
                "python": sys.executable}
    if a.cmd == "shot":
        return cmd_shot(a)
    if a.cmd == "windows":
        wins = dc.list_visible_windows()
        return {"ok": True, "count": len(wins), "windows": wins}
    if a.cmd == "foreground":
        return dc.foreground_info()
    if a.cmd == "pos":
        x, y = dc.cursor_pos()
        return {"ok": True, "x": x, "y": y}
    if a.cmd == "activate":
        return dc.activate_app(a.app)
    if a.cmd == "focus":
        return dc.focus_window(a.hwnd, a.pid)
    if a.cmd == "move":
        x, y = dc.move(a.x, a.y)
        return {"ok": True, "x": x, "y": y}
    if a.cmd == "click":
        x, y = dc.click(a.x, a.y, a.button, a.double, a.mods or [])
        return {"ok": True, "x": x, "y": y}
    if a.cmd == "drag":
        x, y = dc.drag(a.x1, a.y1, a.x2, a.y2)
        return {"ok": True, "x": x, "y": y}
    if a.cmd == "scroll":
        dc.scroll(a.dy, a.dx)
        return {"ok": True}
    if a.cmd == "key":
        dc.send_combo(a.combo)
        return {"ok": True}
    if a.cmd == "type":
        return cmd_type(a)
    if a.cmd == "wait":
        time.sleep(max(0, a.ms) / 1000.0)
        return {"ok": True, "waited_ms": a.ms}
    raise ValueError("不认识的子命令: " + a.cmd)


def build_parser():
    p = argparse.ArgumentParser(prog="pc.py", description="桌面手眼 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping")
    sp = sub.add_parser("guard")
    sp.add_argument("action", nargs="?", default="status", choices=("on", "off", "status"))
    sp.add_argument("--ttl", type=float, default=None, help="占用秒数，默认 on=60")
    sub.add_parser("home")
    sp = sub.add_parser("batch")
    sp.add_argument("steps", help='JSON 数组，每项是一条命令的 argv，如 [["click","800","450"],["wait","300"]]')
    sp = sub.add_parser("shot")
    sp.add_argument("--out")
    sp.add_argument("--region", help="x,y,w,h（虚拟屏幕物理像素）")
    sp.add_argument("--window", type=int, help="窗口句柄 HWND")
    sp.add_argument("--scale", type=int, default=1, help="最近邻放大倍数，建议配 --region")

    sub.add_parser("windows")
    sub.add_parser("foreground")
    sub.add_parser("pos")

    sp = sub.add_parser("activate")
    sp.add_argument("app")

    sp = sub.add_parser("focus")
    sp.add_argument("hwnd", type=int)
    sp.add_argument("--pid", type=int, default=0)

    sp = sub.add_parser("move")
    sp.add_argument("x", type=int)
    sp.add_argument("y", type=int)

    sp = sub.add_parser("click")
    sp.add_argument("x", type=int)
    sp.add_argument("y", type=int)
    sp.add_argument("--button", default="left", choices=("left", "right"))
    sp.add_argument("--double", action="store_true")
    sp.add_argument("--mods", nargs="*", default=None)

    sp = sub.add_parser("drag")
    sp.add_argument("x1", type=int)
    sp.add_argument("y1", type=int)
    sp.add_argument("x2", type=int)
    sp.add_argument("y2", type=int)

    sp = sub.add_parser("scroll")
    sp.add_argument("dy", type=int)
    sp.add_argument("dx", type=int, default=0)

    sp = sub.add_parser("key")
    sp.add_argument("combo")

    sp = sub.add_parser("type")
    sp.add_argument("text")
    sp.add_argument("--mode", choices=("paste", "line"), default="paste")
    sp.add_argument("--clear", action="store_true")

    sp = sub.add_parser("wait")
    sp.add_argument("ms", type=int)
    return p


def emit(payload, code=0):
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    sys.exit(code)


def main():
    a = build_parser().parse_args()
    try:
        res = run(a)
    except dc.ElevatedWindow as e:
        emit({"ok": False, "code": "elevated", "hwnd": e.hwnd,
              "window": e.window, "pid": e.pid, "error": str(e)}, 2)
    except Exception as e:
        emit({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:300])}, 1)
    res.setdefault("ok", True)
    emit(res, 0)


if __name__ == "__main__":
    main()
