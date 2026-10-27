#!/usr/bin/env python3
"""Acceptance check 9.7 -- same-named INI, optional log, SkipIntro.

    python tools/test_config.py --game-dir "D:\\Games\\DK4HD"

Phases:
  1. no INI/log -> default INI is created (seven keys, Chinese comments), no log
  2. Log=1, SkipIntro=1 -> log exists, config line and three skip patches
  3. Log=0 -> no log file is created
The original INI/log are restored afterwards.
"""

import argparse
import os
import re
import sys
import time

from _dk4 import (
    backup_files,
    find_window,
    focus_window,
    read_ini,
    read_text,
    start_game,
    stop_game,
    write_test_ini,
)


def parse_args():
    p = argparse.ArgumentParser(description="DK4HD INI/log/skip-intro acceptance")
    p.add_argument("--game-dir", default=r"E:\Games\DK4HD")
    p.add_argument("--exe", default="DK4HD_tc.exe")
    p.add_argument("--asi-name", default="DK4HD_Fix")
    p.add_argument("--settle-seconds", type=float, default=2.5)
    p.add_argument("--screenshot", action="store_true")
    return p.parse_args()


def screenshot(path):
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=True)
        img.save(path)
        print(f"screenshot: {path}")
    except Exception as exc:
        print(f"WARNING: screenshot skipped: {exc}")


def main():
    args = parse_args()
    game_dir = os.path.abspath(args.game_dir)
    exe_path = os.path.join(game_dir, args.exe)
    ini_path = os.path.join(game_dir, args.asi_name + ".ini")
    log_path = os.path.join(game_dir, args.asi_name + ".log")
    if not os.path.isfile(exe_path):
        print(f"FAIL: {exe_path} not found")
        return 2

    failures = 0

    def fail(msg):
        nonlocal failures
        failures += 1
        print(f"[FAIL] {msg}")

    def passed(msg):
        print(f"[PASS] {msg}")

    with backup_files([ini_path, log_path]):
        # ---- phase 1: default INI, no log --------------------------------
        for path in (ini_path, log_path):
            try:
                os.remove(path)
            except OSError:
                pass

        proc = start_game(exe_path, game_dir)
        time.sleep(args.settle_seconds)
        if proc.poll() is not None:
            fail(f"phase1: {args.exe} exited early")
        stop_game(proc)

        ini_text = read_text(ini_path)
        if not ini_text:
            fail("phase1: default INI was not created")
        else:
            values = read_ini(ini_path)
            expected = {
                "framepacing": 1,
                "cpuidle": 1,
                "clicklatch": 1,
                "clickrelease": 1,
                "skipintro": 0,
                "log": 0,
                "60fps": 0,
            }
            for key, want in expected.items():
                got = values.get(key)
                if got == want:
                    passed(f"phase1: {key}={got}")
                else:
                    fail(f"phase1: {key} expected {want}, got {got!r}")
            if "启用" in ini_text:
                passed("phase1: Chinese comments present")
            else:
                fail("phase1: Chinese comments missing")
        if os.path.exists(log_path):
            fail("phase1: log created with Log=0")
        else:
            passed("phase1: no log with Log=0")

        # ---- phase 2: Log=1, SkipIntro=1 ---------------------------------
        write_test_ini(ini_path, log=1, skip=1)
        try:
            os.remove(log_path)
        except OSError:
            pass

        proc = start_game(exe_path, game_dir)
        time.sleep(args.settle_seconds)
        if proc.poll() is not None:
            fail(f"phase2: {args.exe} exited early")
        hwnd = find_window(proc.pid, timeout=5.0)
        if not hwnd:
            fail("phase2: game window not found")
        elif args.screenshot:
            focus_window(hwnd)
            time.sleep(0.5)
            screenshot(os.path.join(os.getcwd(), "skip_intro.png"))
        stop_game(proc)

        log_text = read_text(log_path)
        if not log_text:
            fail("phase2: log missing with Log=1")
        else:
            config_line = next(
                (ln for ln in log_text.splitlines() if ln.startswith("config: ")), "")
            expected_fields = (
                "FramePacing=1", "CpuIdle=1", "ClickLatch=1", "ClickRelease=1",
                "SkipIntro=1", "Log=1", "60fps=0",
            )
            if all(field in config_line for field in expected_fields):
                passed("phase2: config line")
            else:
                fail("phase2: config line missing/wrong")
            skip_lines = [ln for ln in log_text.splitlines()
                          if re.match(r"^skip intro: .*patched at", ln)]
            if len(skip_lines) >= 3:
                passed("phase2: three skip-intro patches")
            else:
                fail(f"phase2: expected 3 skip-intro patches, got {len(skip_lines)}")
            if re.search(r"(?m)^release fix: poll\+0x42 patched at", log_text):
                passed("phase2: release patch")
            else:
                fail("phase2: release patch line missing")

        # ---- phase 3: Log=0 ----------------------------------------------
        write_test_ini(ini_path, log=0, skip=1)
        try:
            os.remove(log_path)
        except OSError:
            pass

        proc = start_game(exe_path, game_dir)
        time.sleep(args.settle_seconds)
        if proc.poll() is not None:
            fail(f"phase3: {args.exe} exited early")
        stop_game(proc)
        if os.path.exists(log_path):
            fail("phase3: log created with Log=0")
        else:
            passed("phase3: no log with Log=0")

    if failures:
        print(f"\n{failures} assertion(s) failed.")
        return 1
    print("\nAll config/log/skip-intro assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
