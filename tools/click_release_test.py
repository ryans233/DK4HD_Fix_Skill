#!/usr/bin/env python3
"""Acceptance check 9.6 -- press/release edge pairing for the left button.

Starts the game, focuses its window, and uses Frida to count the return values
of the game's button-edge function (3 = down, 1 = up) while sending clicks
without moving the cursor. Passes when presses == releases == --repeats.

    pip install frida
    python tools/click_release_test.py --game-dir "D:\\Games\\DK4HD" --exe DK4HD_tc.exe

The script does not modify the INI or the log. To reproduce the "stuck button"
control case, set ClickLatch=1 and ClickRelease=0 in DK4HD_Fix.ini, then
restart the game and run this script again; releases should be fewer.
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

from _dk4 import ensure_no_game_running


EDGE_RVA = {
    "DK4HD_tc.exe": 0x41BB0,
    "DK4HD_jp.exe": 0x41BB0,
    "DK4HD_sc.exe": 0x41BB0,
    "DK4HD_kr.exe": 0x41D20,
}


def parse_args():
    p = argparse.ArgumentParser(description="DK4HD press/release edge test")
    p.add_argument("--game-dir", default=r"E:\Games\DK4HD")
    p.add_argument("--exe", default="DK4HD_tc.exe", choices=sorted(EDGE_RVA))
    p.add_argument("--hold-ms", type=int, default=5, help="left-button hold time")
    p.add_argument("--repeats", type=int, default=10)
    p.add_argument("--gap-ms", type=int, default=400)
    p.add_argument("--settle-seconds", type=float, default=2.0)
    return p.parse_args()


def find_window(pid, timeout=30.0):
    user32 = ctypes.windll.user32
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def enum_cb(hwnd, _):
            owner = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls, 256)
                found.append((hwnd, cls.value))
            return True

        user32.EnumWindows(enum_cb, 0)
        for hwnd, cls in found:
            if cls == "KT_DK4_PK_HD_WINDOW_NAME":
                return hwnd
        time.sleep(0.25)
    return None


def focus_window(hwnd):
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.ShowWindow(hwnd, 9)
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0041)
    user32.BringWindowToTop(hwnd)
    fg = user32.GetForegroundWindow()
    t1 = user32.GetWindowThreadProcessId(fg, None)
    t2 = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(t2, t1, True)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)
    user32.AttachThreadInput(t2, t1, False)


def center_client(hwnd):
    user32 = ctypes.windll.user32
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    pt = wt.POINT((rect.right - rect.left) // 2, (rect.bottom - rect.top) // 2)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def main():
    args = parse_args()
    game_dir = os.path.abspath(args.game_dir)
    exe = os.path.join(game_dir, args.exe)
    if not os.path.isfile(exe):
        print(f"FAIL: {exe} not found")
        return 2
    if not ensure_no_game_running():
        return 2

    try:
        import frida
    except ImportError:
        print("FAIL: Python package 'frida' is required (pip install frida)")
        return 2

    import subprocess

    proc = subprocess.Popen([exe], cwd=game_dir)
    hwnd = find_window(proc.pid)
    if not hwnd:
        proc.terminate()
        print("FAIL: game window not found")
        return 2

    focus_window(hwnd)
    time.sleep(args.settle_seconds)

    js = f"""
const BASE = Process.getModuleByName('{args.exe.lower()}').base;
let presses = 0, releases = 0;
Interceptor.attach(BASE.add({EDGE_RVA[args.exe]}), {{
  onEnter(a) {{ this.btn = a[1].toInt32(); }},
  onLeave(r) {{
    if (this.btn !== 0) return;
    const v = r.toInt32();
    if (v === 3) presses++;
    else if (v === 1) releases++;
  }}
}});
rpc.exports.stats = function () {{ return [presses, releases]; }};
"""
    session = frida.attach(proc.pid)
    script = session.create_script(js)
    script.load()

    x, y = center_client(hwnd)
    user32 = ctypes.windll.user32
    user32.SetCursorPos(x, y)
    time.sleep(0.5)

    try:
        for _ in range(args.repeats):
            user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
            time.sleep(args.hold_ms / 1000.0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
            time.sleep(args.gap_ms / 1000.0)
        time.sleep(0.6)
        presses, releases = script.exports_sync.stats()
    finally:
        try:
            script.unload()
        except Exception:
            pass
        try:
            session.detach()
        except Exception:
            pass
        proc.terminate()
        time.sleep(0.4)

    print(f"hold={args.hold_ms}ms repeats={args.repeats} "
          f"presses={presses} releases={releases}")
    if presses == args.repeats and releases == args.repeats:
        print("[PASS] press/release edges are paired")
        return 0
    print("[FAIL] press/release edges are not paired; "
          "check ClickLatch/ClickRelease and window focus")
    return 1


if __name__ == "__main__":
    sys.exit(main())
