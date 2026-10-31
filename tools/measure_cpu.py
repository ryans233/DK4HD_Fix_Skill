#!/usr/bin/env python3
"""Acceptance check 9.3 -- hottest-thread CPU usage.

    python tools/measure_cpu.py --game-dir "D:\\Games\\DK4HD"

Samples per-thread CPU time with psutil while the game idles at the main menu.
Percentages are per logical core (100 = one fully busy core). The unfixed build
spins one thread at ~100%.  The script backs up the INI, writes a temporary
main-menu 30Hz INI (SkipIntro=1, 60fps=0), and restores it afterwards.  Fast60
is ~10-12% and is not gated by this check.
"""

import argparse
import os
import sys
import time

from _dk4 import (backup_files, ensure_no_game_running, start_game,
                  stop_game, write_test_ini)


def parse_args():
    p = argparse.ArgumentParser(description="DK4HD thread CPU acceptance")
    p.add_argument("--game-dir", default=r"E:\Games\DK4HD")
    p.add_argument("--exe", default="DK4HD_tc.exe")
    p.add_argument("--settle-seconds", type=float, default=10.0)
    p.add_argument("--samples", type=int, default=6)
    p.add_argument("--interval-ms", type=int, default=1000)
    p.add_argument("--max-hot-thread", type=float, default=12.0)
    p.add_argument("--asi-name", default="DK4HD_Fix")
    return p.parse_args()


def snapshot(proc):
    # psutil returns cumulative seconds per thread; sum user + system.
    result = {}
    for thread in proc.threads():
        result[thread.id] = thread.user_time + thread.system_time
    return result


def main():
    args = parse_args()
    game_dir = os.path.abspath(args.game_dir)
    exe_path = os.path.join(game_dir, args.exe)
    if not os.path.isfile(exe_path):
        print(f"FAIL: {exe_path} not found")
        return 2
    if not ensure_no_game_running():
        return 2

    try:
        import psutil
    except ImportError:
        print("FAIL: Python package 'psutil' is required (pip install psutil)")
        return 2

    ini_path = os.path.join(game_dir, args.asi_name + ".ini")
    log_path = os.path.join(game_dir, args.asi_name + ".log")
    with backup_files([ini_path, log_path]):
        # 9.3 is a main-menu measurement: SkipIntro=1, 30Hz, no log.
        write_test_ini(ini_path, skip=1, log=0, fps=0, enum=1)
        print("test INI: FramePacing=1 CpuIdle=1 ClickLatch=1 ClickRelease=1 "
              "EnumCache=1 SkipIntro=1 Log=0 60fps=0")

        proc = start_game(exe_path, game_dir)
        try:
            time.sleep(args.settle_seconds)
            if proc.poll() is not None:
                print(f"FAIL: {args.exe} exited early (code {proc.returncode})")
                return 2

            ps = psutil.Process(proc.pid)
            previous = snapshot(ps)
            peak = {}
            interval = args.interval_ms / 1000.0

            for _ in range(args.samples):
                time.sleep(interval)
                current = snapshot(ps)
                for thread_id, current_time in current.items():
                    old_time = previous.get(thread_id)
                    if old_time is None:
                        continue
                    percent = max(0.0, (current_time - old_time) / interval * 100.0)
                    peak[thread_id] = max(peak.get(thread_id, 0.0), percent)
                previous = current
        finally:
            stop_game(proc)

    if not peak:
        print("FAIL: no thread samples collected")
        return 1

    top = sorted(peak.items(), key=lambda item: item[1], reverse=True)[:3]
    for thread_id, percent in top:
        print(f"thread {thread_id:<8} {percent:6.1f}% of one core")

    hottest = top[0][1]
    if hottest <= args.max_hot_thread:
        print(f"[PASS] hottest thread {hottest:.1f}% <= {args.max_hot_thread}% of one core")
        return 0
    print(f"[FAIL] hottest thread {hottest:.1f}% of one core; "
          "the time-source spin is probably still active")
    return 1


if __name__ == "__main__":
    sys.exit(main())
