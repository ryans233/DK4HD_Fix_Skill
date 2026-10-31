#!/usr/bin/env python3
"""Acceptance check 9.2 -- frame pacing via PresentMon.

    python tools/measure_frames.py --game-dir "D:\\Games\\DK4HD"

Launches the game, lets it settle, records a 15 s PresentMon capture with
--v1_metrics, and checks msBetweenPresents percentiles. PresentMon 2.6.0 is
downloaded from the official Intel release when --presentmon is not supplied.

Run this with 60fps=0 (30Hz).  Fast60 is covered by tools/fast60_test.py (9.9).
"""

import argparse
import csv
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

from _dk4 import start_game, stop_game


PRESENTMON_URL = (
    "https://github.com/GameTechDev/PresentMon/releases/download/"
    "v2.6.0/PresentMon-2.6.0-x64.exe"
)


def parse_args():
    p = argparse.ArgumentParser(description="DK4HD frame-pacing acceptance")
    p.add_argument("--game-dir", default=r"E:\Games\DK4HD")
    p.add_argument("--exe", default="DK4HD_tc.exe")
    p.add_argument("--settle-seconds", type=float, default=18.0)
    p.add_argument("--seconds", type=int, default=15)
    p.add_argument("--presentmon", default="")
    p.add_argument("--csv", default="")
    p.add_argument("--p50-min", type=float, default=32.5)
    p.add_argument("--p50-max", type=float, default=33.5)
    p.add_argument("--p95-max", type=float, default=34.0)
    return p.parse_args()


def percentile(values, pct):
    values = sorted(values)
    if not values:
        return 0.0
    k = (len(values) - 1) * pct / 100.0
    f = int(k)
    c = min(f + 1, len(values) - 1)
    return values[f] + (values[c] - values[f]) * (k - f)


def ensure_presentmon(path):
    if path:
        return path
    candidate = os.path.join(tempfile.gettempdir(), "PresentMon-2.6.0-x64.exe")
    if not os.path.isfile(candidate):
        print("Downloading PresentMon 2.6.0 ...")
        urllib.request.urlretrieve(PRESENTMON_URL, candidate)
    return candidate


def main():
    args = parse_args()
    game_dir = os.path.abspath(args.game_dir)
    exe_path = os.path.join(game_dir, args.exe)
    if not os.path.isfile(exe_path):
        print(f"FAIL: {exe_path} not found")
        return 2
    presentmon = ensure_presentmon(args.presentmon)
    if not os.path.isfile(presentmon):
        print(f"FAIL: PresentMon not found: {presentmon}")
        return 2

    csv_path = args.csv or os.path.join(
        tempfile.gettempdir(),
        f"dk4hd_frames_{os.path.splitext(args.exe)[0]}.csv")
    try:
        os.remove(csv_path)
    except OSError:
        pass

    proc = start_game(exe_path, game_dir)
    time.sleep(args.settle_seconds)
    if proc.poll() is not None:
        print(f"FAIL: {args.exe} exited before capture (code {proc.returncode})")
        stop_game(proc)
        return 2

    try:
        subprocess.run(
            [presentmon, "--process_id", str(proc.pid), "--output_file", csv_path,
             "--v1_metrics", "--no_console_stats", "--timed", str(args.seconds),
             "--terminate_after_timed"],
            check=False,
        )
    finally:
        stop_game(proc)

    if not os.path.isfile(csv_path):
        print(f"FAIL: no capture written to {csv_path}")
        return 1

    frames = []
    sync_values = set()
    with open(csv_path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        for row in csv.DictReader(f):
            raw = (row.get("msBetweenPresents") or "").strip()
            if raw:
                try:
                    frames.append(float(raw))
                except ValueError:
                    pass
            sync = (row.get("SyncInterval") or "").strip()
            if sync:
                sync_values.add(sync)

    if len(frames) < 60:
        print(f"FAIL: only {len(frames)} frames captured; is the game presenting?")
        return 1

    p5 = percentile(frames, 5)
    p50 = percentile(frames, 50)
    p95 = percentile(frames, 95)
    max_frame = max(frames)
    slow_band = sum(1 for ms in frames if 45.0 <= ms <= 49.5) / len(frames)
    fps = 1000.0 / p50 if p50 else 0.0

    print(f"frames={len(frames)}  p5={p5:.2f}ms  p50={p50:.2f}ms  "
          f"p95={p95:.2f}ms  max={max_frame:.1f}ms  fps(p50)={fps:.1f}")
    print(f"SyncInterval={','.join(sorted(sync_values))}  "
          f"45-49.5ms share={slow_band:.1%}  frames>100ms="
          f"{sum(1 for ms in frames if ms > 100.0)}")

    ok = True
    if sync_values != {"1"}:
        print("[FAIL] SyncInterval is not 1")
        ok = False
    if not (args.p50_min <= p50 <= args.p50_max):
        print(f"[FAIL] p50 outside {args.p50_min}..{args.p50_max} ms")
        ok = False
    if p95 > args.p95_max:
        print(f"[FAIL] p95 above {args.p95_max} ms")
        ok = False
    if slow_band > 0.01:
        print("[FAIL] more than 1% of frames land in the 45-49.5 ms quantization band")
        ok = False
    if not (29.5 <= fps <= 31.0):
        print(f"[FAIL] p50 frame rate is {fps:.1f} fps, expected ~30")
        ok = False

    if ok:
        print("[PASS] frame pacing is smooth (no 31.25/46.875 ms quantization)")
        return 0
    print(f"Capture kept at {csv_path}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
