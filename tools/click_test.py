#!/usr/bin/env python3
"""Acceptance check 9.4 -- short-click latch.

    python tools/click_test.py --game-dir "D:\\Games\\DK4HD" \
        --trace-asi build\\DK4HD_Fix_trace.asi

Sends 5/10/20/40/80 ms left clicks (12 per hold), then pairs the trace log
lines written by the -DDK4HD_TRACE_CLICKS build:

    latch msg=0x0201 hwnd=...
    inject dev=0x... age=<ms>

Without --trace-asi the script only sends the click train; judge in game.
The INI is backed up and restored; the test log is kept for inspection.
"""

import argparse
import os
import re
import shutil
import sys
import time

from _dk4 import (
    backup_files,
    client_center,
    find_window,
    focus_window,
    read_text,
    start_game,
    stop_game,
    user32,
    write_test_ini,
)


def parse_args():
    p = argparse.ArgumentParser(description="DK4HD short-click latch acceptance")
    p.add_argument("--game-dir", default=r"E:\Games\DK4HD")
    p.add_argument("--exe", default="DK4HD_tc.exe")
    p.add_argument("--asi-name", default="DK4HD_Fix")
    p.add_argument("--hold-ms", nargs="+", type=int, default=[5, 10, 20, 40, 80])
    p.add_argument("--repeats", type=int, default=12)
    p.add_argument("--gap-ms", type=int, default=400)
    p.add_argument("--settle-seconds", type=float, default=12.0)
    p.add_argument("--trace-asi", default="")
    return p.parse_args()


def main():
    args = parse_args()
    game_dir = os.path.abspath(args.game_dir)
    exe_path = os.path.join(game_dir, args.exe)
    asi_path = os.path.join(game_dir, args.asi_name + ".asi")
    ini_path = os.path.join(game_dir, args.asi_name + ".ini")
    log_path = os.path.join(game_dir, args.asi_name + ".log")
    if not os.path.isfile(exe_path):
        print(f"FAIL: {exe_path} not found")
        return 2

    trace_backup = None
    use_trace = bool(args.trace_asi)
    if use_trace:
        trace_asi = os.path.abspath(args.trace_asi)
        if not os.path.isfile(trace_asi):
            print(f"FAIL: {trace_asi} not found")
            return 2
        if os.path.isfile(asi_path):
            trace_backup = asi_path + ".release.bak"
            shutil.copy2(asi_path, trace_backup)
        shutil.copy2(trace_asi, asi_path)

    try:
        with backup_files([ini_path, log_path]):
            write_test_ini(ini_path, log=1, skip=0)
            try:
                os.remove(log_path)
            except OSError:
                pass

            proc = start_game(exe_path, game_dir)
            try:
                time.sleep(args.settle_seconds)
                if proc.poll() is not None:
                    print(f"FAIL: {args.exe} exited early (code {proc.returncode})")
                    return 2
                hwnd = find_window(proc.pid, timeout=10)
                if not hwnd:
                    print("FAIL: no main window handle")
                    return 2
                focus_window(hwnd)
                time.sleep(0.7)
                x, y = client_center(hwnd)
                user32.SetCursorPos(x, y)
                time.sleep(0.3)

                print(f"Sending {args.repeats} clicks per hold: {args.hold_ms}")
                for hold in args.hold_ms:
                    for _ in range(args.repeats):
                        user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
                        time.sleep(hold / 1000.0)
                        user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
                        time.sleep(args.gap_ms / 1000.0)
                    print(f"  sent hold={hold}")
                time.sleep(0.8)
            finally:
                stop_game(proc)

            if not use_trace:
                print("Trace build not supplied (--trace-asi); click train finished, "
                      "verify the response in game.")
                return 0

            log = read_text(log_path, timeout=2.0)
            lines = [ln.strip() for ln in log.splitlines()
                     if ln.startswith("latch ") or ln.startswith("inject ")]
            latches = [ln for ln in lines if ln.startswith("latch ")]
            injects = [ln for ln in lines if ln.startswith("inject ")]
            if not latches:
                print("FAIL: no latch lines in the log; was the trace build loaded?")
                return 1

            pending = []
            credited = [False] * len(latches)
            ages = []
            duplicates = 0
            next_latch = 0
            for line in lines:
                if line.startswith("latch "):
                    pending.append(next_latch)
                    next_latch += 1
                else:
                    if not pending:
                        duplicates += 1
                    else:
                        idx = pending.pop(0)
                        credited[idx] = True
                        m = re.search(r"age=(\d+)", line)
                        if m:
                            ages.append(int(m.group(1)))

            expected = len(args.hold_ms) * args.repeats
            ok = True
            print()
            for group, hold in enumerate(args.hold_ms):
                start = group * args.repeats
                end = min(start + args.repeats, len(credited))
                hits = sum(1 for v in credited[start:end] if v)
                need = min(10, args.repeats)
                verdict = "PASS" if hits >= need else "FAIL"
                if verdict == "FAIL":
                    ok = False
                print(f"hold={hold:3d} ms  {hits}/{args.repeats} injected  [{verdict}]")

            median_age = 0
            if ages:
                ages.sort()
                median_age = ages[len(ages) // 2]
            print(f"latches={len(latches)} (expected {expected})  "
                  f"injections={len(injects)}  duplicates={duplicates}  "
                  f"median age={median_age} ms")

            if len(latches) < expected:
                print("[FAIL] some clicks never produced a window message")
                ok = False
            if duplicates > 0:
                print(f"[FAIL] {duplicates} injected down edge(s) without a pending click")
                ok = False
            if ok:
                print("[PASS] short clicks are latched and injected exactly once")
                return 0
            return 1
    finally:
        if use_trace:
            if trace_backup:
                shutil.copy2(trace_backup, asi_path)
                os.remove(trace_backup)
            elif os.path.exists(asi_path):
                os.remove(asi_path)


if __name__ == "__main__":
    sys.exit(main())
