#!/usr/bin/env python3
r"""Acceptance check 9.10 -- device enumeration cache (`EnumCache`).

The game calls IDirectInput8::EnumDevices every ~2.6s while it is the foreground
window.  With a per-process HID filter (HIDHide and friends) or a slow HID
stack that call costs 445-450ms on the frame thread, so the title drops to
39-40 FPS; the same executable renamed to a whitelisted path takes 4-7ms.

Checks, per executable (EnumCache=1):
  1. log chain: `dinput enum hook: 0`, `hooked IDirectInput8::EnumDevices`,
     `dinput enum: captured ... cached`;
  2. `dinput enum: replayed ...`, or a game controller was captured
     (`type=0x4` >= 1 device) so the game itself stopped polling;
  3. focused PresentMon window has no frame gap >100ms and fg_ratio >= 0.9.

A/B (up to --ab-limit executables, stops at the first slow baseline): the same
executable with EnumCache=0.  A baseline with >100ms frames must disappear with
the cache; a baseline without them reports N/A (fast HID stack, or the exe can
see the controller).

HIDHide is detected up front and reported (driver, service, client, watchdog,
config key readability); it is informational only.

Usage (from repo root):
  python tools/enumcache_test.py --game-dir "E:\Games\DK4HD"
  python tools/enumcache_test.py --game-dir "E:\Games\DK4HD" --exe DK4HD_sc.exe
  python tools/enumcache_test.py --game-dir "E:\Games\DK4HD" --no-ab --no-presentmon
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import statistics
import subprocess
import sys
import time

from _dk4 import (backup_files, ensure_no_game_running, find_window, focus_window,
                  process_names, read_text, start_game, stop_game, write_test_ini)

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "out")
os.makedirs(OUTDIR, exist_ok=True)
PRESENTMON = os.environ.get("PRESENTMON", os.path.join(HERE, "PresentMon.exe"))
ALL_EXES = ["DK4HD_tc.exe", "DK4HD_kr.exe", "DK4HD_jp.exe", "DK4HD_sc.exe"]

STALL_MS = 100.0     # a healthy 30Hz frame is ~33ms (limiter >=33 + spin)
FG_MIN = 0.9         # foreground ratio required during the sample window

RE_CAPTURE = re.compile(
    r"dinput enum: captured (\d+) device\(s\) type=0x([0-9A-Fa-f]+) "
    r"flags=0x([0-9A-Fa-f]+) in (\d+) ms, cached")

HIDHIDE_PARAM_KEY = r"SYSTEM\CurrentControlSet\Services\HidHide\Parameters"
HIDHIDE_DIR = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                           "Nefarius Software Solutions", "HidHide")

USER32 = ctypes.windll.user32


# ---------------------------------------------------------------------------
# environment: HIDHide (per-process HID filtering explains the by-name effect)
# ---------------------------------------------------------------------------
class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [("dwServiceType", wt.DWORD), ("dwCurrentState", wt.DWORD),
                ("dwControlsAccepted", wt.DWORD), ("dwWin32ExitCode", wt.DWORD),
                ("dwServiceSpecificExitCode", wt.DWORD), ("dwCheckPoint", wt.DWORD),
                ("dwWaitHint", wt.DWORD)]


def _service_state(name):
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.OpenSCManagerW.restype = wt.HANDLE
    advapi32.OpenSCManagerW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.DWORD]
    advapi32.OpenServiceW.restype = wt.HANDLE
    advapi32.OpenServiceW.argtypes = [wt.HANDLE, wt.LPCWSTR, wt.DWORD]
    advapi32.QueryServiceStatus.argtypes = [wt.HANDLE, ctypes.POINTER(SERVICE_STATUS)]
    advapi32.CloseServiceHandle.argtypes = [wt.HANDLE]
    states = {1: "STOPPED", 2: "START_PENDING", 3: "STOP_PENDING", 4: "RUNNING",
              5: "CONTINUE_PENDING", 6: "PAUSE_PENDING", 7: "PAUSED"}
    scm = advapi32.OpenSCManagerW(None, None, 0x0001)      # SC_MANAGER_CONNECT
    if not scm:
        return None
    try:
        svc = advapi32.OpenServiceW(scm, name, 0x0004)     # SERVICE_QUERY_STATUS
        if not svc:
            return None
        try:
            status = SERVICE_STATUS()
            if not advapi32.QueryServiceStatus(svc, ctypes.byref(status)):
                return None
            return states.get(status.dwCurrentState, str(status.dwCurrentState))
        finally:
            advapi32.CloseServiceHandle(svc)
    finally:
        advapi32.CloseServiceHandle(scm)


def _config_readable():
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.RegOpenKeyExW.argtypes = [wt.HANDLE, wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                       ctypes.POINTER(wt.HANDLE)]
    advapi32.RegCloseKey.argtypes = [wt.HANDLE]
    key = wt.HANDLE()
    rc = advapi32.RegOpenKeyExW(wt.HANDLE(0x80000002), HIDHIDE_PARAM_KEY, 0, 0x20019,
                                ctypes.byref(key))
    if rc == 0:
        advapi32.RegCloseKey(key)
        return True
    return False if rc == 5 else None            # 5 = ERROR_ACCESS_DENIED


def detect_hidhide():
    root = os.environ.get("SystemRoot", r"C:\Windows")
    info = {
        "driver_file": os.path.isfile(os.path.join(root, "System32", "drivers",
                                                   "hidhide.sys")),
        "service_state": _service_state("HidHide"),
        "client_install": HIDHIDE_DIR if os.path.isfile(
            os.path.join(HIDHIDE_DIR, "x64", "HidHideCLI.exe")) else None,
        "running": sorted(n for n in process_names() if n.startswith("hidhide")),
        "config_readable": _config_readable(),
    }
    info["present"] = bool(info["driver_file"] or info["service_state"]
                           or info["client_install"] or info["running"])
    return info


def print_hidhide_notice(info):
    if not info.get("present"):
        return
    bits = []
    if info.get("service_state"):
        bits.append(f"service HidHide={info['service_state']}")
    if info.get("driver_file"):
        bits.append("hidhide.sys present")
    if info.get("running"):
        bits.append("running: " + ", ".join(info["running"]))
    if info.get("client_install"):
        bits.append("client: " + os.path.join(info["client_install"], "x64"))
    print("\n[!] HIDHide detected (" + "; ".join(bits) + ")")
    print("    The ~2.6s EnumDevices poll walks every HID device the process can")
    print("    see (20-62ms each, so many devices means hundreds of ms -> title")
    print("    drops while focused).  HIDHide can hide these devices from selected")
    print("    exes: a hidden exe enumerates 0 devices and skips the walk (fast),")
    print("    an exe that still sees them pays the full walk every poll.")
    if info.get("config_readable") is False:
        print("    Whitelist: " + HIDHIDE_PARAM_KEY + " (ACL-protected; use the")
        print("    client or 'HidHideCLI.exe --app-list' from an elevated prompt).")
    print("    -> EnumCache=1 covers this.  To exercise the replay path and the")
    print("       A/B baseline, unhide the exe (or whitelist it) and rerun.\n")


# ---------------------------------------------------------------------------
# sampling
# ---------------------------------------------------------------------------
def init_presentmon(override):
    global PRESENTMON
    if override:
        PRESENTMON = override
        return PRESENTMON
    if os.path.exists(PRESENTMON):
        return PRESENTMON
    try:
        from measure_frames import ensure_presentmon
        PRESENTMON = ensure_presentmon("")
    except Exception as exc:                    # noqa: BLE001
        print(f"warning: PresentMon not available: {exc}")
    return PRESENTMON


def presentmon_sample(exe, seconds, tag, hwnd):
    out = os.path.join(OUTDIR, f"enum_{tag}.csv")
    if os.path.exists(out):
        os.remove(out)
    if not os.path.exists(PRESENTMON):
        return {"error": "PresentMon not found"}
    try:
        proc = subprocess.Popen(
            [PRESENTMON, "--process_name", exe, "--output_file", out,
             "--timed", str(seconds), "--terminate_after_timed",
             "--v1_metrics", "--no_console_stats"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:                    # noqa: BLE001
        return {"error": str(exc)}
    same = total = 0
    while proc.poll() is None:
        if hwnd:
            total += 1
            if USER32.GetForegroundWindow() == hwnd:
                same += 1
        time.sleep(0.2)
    if proc.returncode not in (0, None):
        return {"error": f"PresentMon exit {proc.returncode}"}
    if not os.path.exists(out):
        return {"error": "no PresentMon output"}
    with open(out, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    ms = [float(r["msBetweenPresents"]) for r in rows if r["msBetweenPresents"]]
    if not ms:
        return {"error": "no PresentMon rows"}
    return {
        "frames": len(ms),
        "fps": round(1000.0 / statistics.mean(ms), 1),
        "max_ms": round(max(ms), 2),
        "stalls": sorted((round(x) for x in ms if x > STALL_MS), reverse=True),
        "fg_ratio": round(same / total, 2) if total else None,
    }


def parse_log(text):
    captures = [{"devices": int(m.group(1)), "dev_type": int(m.group(2), 16),
                 "flags": int(m.group(3), 16), "ms": int(m.group(4))}
                for m in RE_CAPTURE.finditer(text)]
    return {
        "enum_hook_ok": "dinput enum hook: 0" in text,
        "vtable_hooked": "hooked IDirectInput8::EnumDevices" in text,
        "captures": len(captures),
        "capture_ms_max": max((c["ms"] for c in captures), default=0),
        "captured_devices": sum(c["devices"] for c in captures),
        "pad_devices": max((c["devices"] for c in captures if c["dev_type"] == 0x4),
                           default=-1),
        "replay_count": text.count("dinput enum: replayed"),
    }


def focus_and_verify(hwnd, tries=6):
    for _ in range(tries):
        focus_window(hwnd)
        if USER32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.5)
    return False


def run_once(exe, game, ini, log, enum_cache, warmup, pm_seconds, do_pm):
    tag = os.path.splitext(exe)[0].replace("DK4HD_", "") + f"_cache{enum_cache}"
    write_test_ini(ini, skip=1, log=1, fps=0, enum=enum_cache)
    try:
        os.remove(log)
    except OSError:
        pass

    proc = start_game(os.path.join(game, exe), game)
    try:
        hwnd = find_window(proc.pid, timeout=25.0)
        if not hwnd:
            return {"exe": exe, "enum_cache": enum_cache, "error": "no window"}
        focused_before = focus_and_verify(hwnd)
        time.sleep(warmup)
        if USER32.GetForegroundWindow() != hwnd:
            focus_and_verify(hwnd)
        pm = presentmon_sample(exe, pm_seconds, tag, hwnd) if do_pm else {}
        focused_after = USER32.GetForegroundWindow() == hwnd
    finally:
        stop_game(proc)
        time.sleep(0.5)

    return {"exe": exe, "enum_cache": enum_cache,
            "focused_before": focused_before, "focused_after": focused_after,
            "presentmon": pm, "log": parse_log(read_text(log))}


def test_exe(exe, game, ini, log, do_pm, do_ab, warmup, pm_seconds):
    cached = run_once(exe, game, ini, log, 1, warmup, pm_seconds, do_pm)
    info = cached.get("log", {})
    pm = cached.get("presentmon", {})
    pad_visible = info.get("pad_devices", -1) >= 1

    checks = {
        "no_error": "error" not in cached,
        "focused": bool(cached.get("focused_before")) and bool(cached.get("focused_after"))
                   and (pm.get("fg_ratio") is None or pm["fg_ratio"] >= FG_MIN),
        "log_enum_hook": bool(info.get("enum_hook_ok")),
        "log_vtable": bool(info.get("vtable_hooked")),
        "log_captured": bool(info.get("captures")),
        # Either the cache was exercised, or the game found a controller and
        # stopped polling -- nothing left to replay in that case.
        "log_replayed": bool(info.get("replay_count")) or pad_visible,
    }
    if do_pm:
        checks["no_stall_over_100ms"] = "error" not in pm and not pm.get("stalls")

    ab = None
    if do_ab and do_pm:
        if pad_visible:
            ab = {"skipped": "controller visible to the exe; the game stops the "
                             "periodic poll, so there is no baseline stall"}
        else:
            base = run_once(exe, game, ini, log, 0, warmup, pm_seconds, do_pm)
            bpm = base.get("presentmon", {})
            base_stalls = list(bpm.get("stalls", []))
            ab = {"baseline_stalls": base_stalls,
                  "baseline_max_ms": bpm.get("max_ms"),
                  "cached_stalls": list(pm.get("stalls", [])),
                  "slow_walk_detected": bool(base_stalls)}
            checks["ab_cache_removes_stall"] = (not pm.get("stalls")) if base_stalls else None

    ok = all(v for v in checks.values() if v is not None)
    return {"exe": exe, "ok": ok, "checks": checks, "cached": cached, "ab": ab}


def main():
    ap = argparse.ArgumentParser(description="DK4HD EnumCache acceptance")
    ap.add_argument("--game-dir", default=r"E:\Games\DK4HD")
    ap.add_argument("--asi-name", default="DK4HD_Fix")
    ap.add_argument("--exe", action="append", default=None)
    ap.add_argument("--warmup", type=float, default=6.0)
    ap.add_argument("--presentmon-seconds", type=int, default=10)
    ap.add_argument("--presentmon", default=None)
    ap.add_argument("--no-presentmon", action="store_true")
    ap.add_argument("--no-ab", action="store_true")
    ap.add_argument("--ab-limit", type=int, default=2)
    args = ap.parse_args()

    game = os.path.abspath(args.game_dir)
    if not os.path.isdir(game):
        print(f"FAIL: game directory not found: {game}")
        return 2
    ini = os.path.join(game, args.asi_name + ".ini")
    log = os.path.join(game, args.asi_name + ".log")

    if not ensure_no_game_running():
        return 2

    hidhide = detect_hidhide()
    print_hidhide_notice(hidhide)

    do_pm = not args.no_presentmon
    if do_pm:
        init_presentmon(args.presentmon)

    exes = args.exe or ALL_EXES
    results = []
    ab_attempts = 0
    ab_satisfied = False
    with backup_files([ini, log]):
        for exe in exes:
            do_ab = (not args.no_ab) and do_pm and not ab_satisfied \
                and ab_attempts < args.ab_limit
            print(f"RUN {exe}{' (+A/B)' if do_ab else ''}", flush=True)
            r = test_exe(exe, game, ini, log, do_pm, do_ab,
                         args.warmup, args.presentmon_seconds)
            results.append(r)
            if r["ab"] and "baseline_stalls" in r["ab"]:
                ab_attempts += 1
                if r["ab"]["slow_walk_detected"]:
                    ab_satisfied = True
            pm = r["cached"].get("presentmon", {})
            info = r["cached"].get("log", {})
            print(json.dumps({
                "exe": exe, "ok": r["ok"], "checks": r["checks"],
                "capture_ms_max": info.get("capture_ms_max"),
                "pad_devices": info.get("pad_devices"),
                "replays": info.get("replay_count"),
                "stalls": pm.get("stalls"), "max_ms": pm.get("max_ms"),
                "fg_ratio": pm.get("fg_ratio"), "ab": r["ab"],
            }, ensure_ascii=False), flush=True)

    if do_pm and not args.no_ab and hidhide.get("present"):
        if not any(r["ab"] and r["ab"].get("slow_walk_detected") for r in results):
            skipped = next((r["ab"]["skipped"] for r in results
                            if r["ab"] and "skipped" in r["ab"]), None)
            print("[i] A/B baseline not reproduced: "
                  + (skipped or "EnumDevices stayed fast with EnumCache=0"))
            print("    HIDHide is active, so this means the tested exe can see the")
            print("    controller (or the walk is cheap here), not that the local HID")
            print("    stack is fast in general.")

    out = os.path.join(OUTDIR, "enumcache_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"environment": {"hidhide": hidhide}, "results": results},
                  f, ensure_ascii=False, indent=2)
    print(f"results: {os.path.relpath(out, os.path.dirname(HERE))}")

    failed = [r["exe"] for r in results if not r["ok"]]
    if failed:
        print("[FAIL] " + ", ".join(failed))
        return 1
    print("[PASS] EnumCache acceptance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
