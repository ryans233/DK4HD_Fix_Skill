#!/usr/bin/env python3
"""Acceptance check 9.1 -- startup and hook installation (all four exes).

    python tools/check_startup.py --game-dir "D:\\Games\\DK4HD"

The script backs up the INI/log, writes a test INI with Log=1, launches each
executable, waits, and verifies the required log lines. Exit code 0 = pass.
"""

import argparse
import os
import re
import sys
import time

from _dk4 import backup_files, read_text, start_game, stop_game, write_test_ini


DEFAULT_EXES = ["DK4HD_tc.exe", "DK4HD_kr.exe", "DK4HD_jp.exe", "DK4HD_sc.exe"]


def parse_args():
    p = argparse.ArgumentParser(description="DK4HD startup/hook acceptance")
    p.add_argument("--game-dir", default=r"E:\Games\DK4HD")
    p.add_argument("--asi-name", default="DK4HD_Fix")
    p.add_argument("--seconds", type=float, default=8.0)
    p.add_argument("--exe", nargs="+", default=DEFAULT_EXES)
    return p.parse_args()


def main():
    args = parse_args()
    game_dir = os.path.abspath(args.game_dir)
    ini_path = os.path.join(game_dir, args.asi_name + ".ini")
    log_path = os.path.join(game_dir, args.asi_name + ".log")
    failures = 0

    with backup_files([ini_path, log_path]):
        write_test_ini(ini_path, log=1, skip=0)

        for name in args.exe:
            exe_path = os.path.join(game_dir, name)
            if not os.path.isfile(exe_path):
                print(f"[SKIP] {name} (not found in {game_dir})")
                continue

            try:
                os.remove(log_path)
            except OSError:
                pass

            proc = start_game(exe_path, game_dir)
            time.sleep(args.seconds)
            alive = proc.poll() is None
            stop_game(proc)
            log = read_text(log_path, timeout=2.0)

            config_line = next(
                (ln for ln in log.splitlines() if ln.startswith("config: ")), "")
            checks = {
                "process alive": alive,
                "InitializeASI line": bool(re.search(
                    rf"(?m)^InitializeASI exe={re.escape(name)} base=0x?[0-9a-fA-F]+", log)),
                "config line": bool(config_line) and all(
                    f"{key}={value}" in config_line
                    for key, value in (
                        ("FramePacing", 1), ("CpuIdle", 1), ("ClickLatch", 1),
                        ("ClickRelease", 1), ("EnumCache", 1), ("SkipIntro", 0),
                        ("Log", 1), ("60fps", 0),
                    )),
                "qpc init": bool(re.search(r"qpcFreq=\d+ ticksPerMs=[1-9]\d*", log)),
                "skip intro state": bool(re.search(r"(?m)^skip intro: disabled\r?$", log)),
                "time/mouse located": bool(re.search(
                    r"(?m)^time=0x?[0-9a-fA-F]+ mouse=0x?[0-9a-fA-F]+ \((scan|rva)\)", log)),
                "release patch": bool(re.search(
                    r"(?m)^release fix: poll\+0x42 patched at 0x?[0-9a-fA-F]+", log)),
                "mouse poll hooked": bool(re.search(r"(?m)^mouse poll hook: 1\r?$", log)),
                "time hook": bool(re.search(r"(?m)^time hook: 0\b", log)),
                "frame limiter patch": bool(re.search(
                    r"(?m)^frame limiter: >33 -> >=33 at 0x?[0-9a-fA-F]+", log)),
                "frame limiter hook": bool(re.search(
                    r"(?m)^frame limiter elapsed hook: 0\r?$", log)),
                "PeekMessageA hook": bool(re.search(r"(?m)^hook PeekMessageA: 0\r?$", log)),
                "dinput enum hook": bool(re.search(r"(?m)^dinput enum hook: 0\r?$", log)),
                "GetDeviceState hooked": bool(re.search(
                    r"(?m)^hooked mouse GetDeviceState dev=0x?[0-9a-fA-F]+ "
                    r"vt=0x?[0-9a-fA-F]+ real=0x?[0-9a-fA-F]+", log)),
            }

            bad = [key for key, ok in checks.items() if not ok]
            if not bad:
                print(f"[PASS] {name}")
            else:
                failures += 1
                print(f"[FAIL] {name} : missing {', '.join(bad)}")
                if log:
                    for line in log.rstrip().splitlines():
                        print(f"       | {line}")

    if failures:
        print(f"\n{failures} executable(s) failed the startup check.")
        return 1
    print("\nAll executables passed the startup check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
