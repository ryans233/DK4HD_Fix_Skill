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
import os
import sys
import time

from _dk4 import (client_center, ensure_no_game_running, find_window,
                  focus_window, start_game, stop_game, user32)


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

    proc = start_game(exe, game_dir)
    session = None
    script = None
    presses = releases = 0
    try:
        hwnd = find_window(proc.pid)
        if not hwnd:
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

        x, y = client_center(hwnd)
        user32.SetCursorPos(x, y)
        time.sleep(0.5)

        for _ in range(args.repeats):
            user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
            time.sleep(args.hold_ms / 1000.0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
            time.sleep(args.gap_ms / 1000.0)
        time.sleep(0.6)
        presses, releases = script.exports_sync.stats()
    finally:
        if script is not None:
            try:
                script.unload()
            except Exception:
                pass
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass
        stop_game(proc)

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
