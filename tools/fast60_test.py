#!/usr/bin/env python3
r"""Fast60 acceptance test for DK4HD_Fix.

Checks, for each language build:
  1. game starts with a temporary INI (`60fps=1`, `CpuIdle=1`, `SkipIntro=1`,
     `Log=1`) and the ASI logs fast60 enabled;
  2. the title bar reports real Present/s and "Fast60 (2x speed)";
  3. F10 toggles Fast60 -> 30Hz -> Fast60 without losing foreground focus;
  4. PresentMon (default) verifies the CpuIdle=1 Fast60 main-menu frame rate.

The temporary INI/log are backed up and restored.  The performance gate is
applied to the first selected exe (default TC); other languages are functional
checks only.  Gate: p50 <= 16.9ms, p95 <= 17.5ms, fps >= 59.5.  This catches
both the old strict `>33` limiter (17.0ms / 58.8fps) and the per-call Sleep(1)
regression (~19.2ms).

Usage (from repo root):
  python tools/fast60_test.py --game-dir "E:\Games\DK4HD"
  python tools/fast60_test.py --game-dir "E:\Games\DK4HD" --exe DK4HD_kr.exe
  python tools/fast60_test.py --game-dir "E:\Games\DK4HD" --no-presentmon  # functional only
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import ctypes.wintypes as wt
import json
import os
import statistics
import subprocess
import sys
import time

from _dk4 import backup_files

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.environ.get("DK4HD_GAME", r"E:\Games\DK4HD")
OUTDIR = os.path.join(HERE, "out")
os.makedirs(OUTDIR, exist_ok=True)
PRESENTMON = os.environ.get("PRESENTMON", os.path.join(HERE, "PresentMon.exe"))
ALL_EXES = ["DK4HD_tc.exe", "DK4HD_kr.exe", "DK4HD_jp.exe", "DK4HD_sc.exe"]
WINDOW_CLASS = "KT_DK4_PK_HD_WINDOW_NAME"
VK_F10 = 0x79
VK = ctypes.windll.user32
KERNEL32 = ctypes.windll.kernel32

# Performance gate for the Fast60 + CpuIdle=1 main-menu steady state.
# With the `>33` -> `>=33` limiter fix and the deadline-aware spin, p50/p95 are
# both ~16.667ms and PresentMon reports ~59.9-60.0 fps.  The old strict `>33`
# limiter measures 17.0ms / 58.8 fps; per-call Sleep(1) measures ~19.2ms.
FAST60_P50_MAX_MS = 16.9
FAST60_P95_MAX_MS = 17.5
FAST60_MIN_FPS = 59.5


def write_fast60_ini(path: str) -> None:
    """Temporary INI: Fast60 + default CPU idle + SkipIntro -> main menu."""
    text = (
        "; Fast60 acceptance INI (temporary; restored after the test)\n"
        "[Fix]\n"
        "FramePacing=1\n"
        "CpuIdle=1\n"
        "ClickLatch=1\n"
        "ClickRelease=1\n"
        "SkipIntro=1\n"
        "Log=1\n"
        "60fps=1\n"
    )
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(text)


def set_dpi_aware() -> None:
    pass


def find_window(pid: int) -> int | None:
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not VK.IsWindowVisible(hwnd):
            return True
        p = wt.DWORD()
        VK.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid:
            return True
        cls = ctypes.create_unicode_buffer(128)
        VK.GetClassNameW(hwnd, cls, 128)
        if cls.value == WINDOW_CLASS:
            found.append(hwnd)
            return False
        return True

    VK.EnumWindows(cb, 0)
    return found[0] if found else None


def wait_window(pid: int, timeout: float = 20.0) -> int | None:
    end = time.time() + timeout
    while time.time() < end:
        hwnd = find_window(pid)
        if hwnd:
            return hwnd
        time.sleep(0.2)
    return None


def focus(hwnd: int) -> None:
    VK.ShowWindow(hwnd, 9)
    VK.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0043)  # HWND_TOPMOST, NOMOVE/NOSIZE/SHOW
    VK.BringWindowToTop(hwnd)
    fg = VK.GetForegroundWindow()
    t1 = VK.GetWindowThreadProcessId(fg, None)
    t2 = KERNEL32.GetCurrentThreadId()
    VK.AttachThreadInput(t2, t1, True)
    VK.SetForegroundWindow(hwnd)
    VK.SetFocus(hwnd)
    VK.AttachThreadInput(t2, t1, False)
    VK.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0043)  # HWND_NOTOPMOST
    for _ in range(20):
        if VK.GetForegroundWindow() == hwnd:
            break
        time.sleep(0.1)
    time.sleep(0.4)


def title(hwnd: int) -> str:
    b = ctypes.create_unicode_buffer(512)
    VK.GetWindowTextW(hwnd, b, 512)
    return b.value


def foreground_is(hwnd: int) -> bool:
    return VK.GetForegroundWindow() == hwnd


def wait_mode(hwnd: int, want, timeout: float = 4.0) -> tuple[str, str | None]:
    """Poll the title until the mode matches; title updates once per second."""
    end = time.time() + timeout
    text = ""
    mode = None
    while time.time() < end:
        text = title(hwnd)
        _, mode = parse_title_mode(text)
        if want(mode):
            return text, mode
        time.sleep(0.1)
    return text, mode


def press_f10() -> None:
    VK.keybd_event(VK_F10, 0, 0, 0)
    time.sleep(0.08)
    VK.keybd_event(VK_F10, 0, 2, 0)


def read_log(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def parse_title_mode(text: str) -> tuple[int | None, str | None]:
    # Example: "... [60 FPS | Fast60 (2x speed)]"
    try:
        body = text.rsplit("[", 1)[1].split("]", 1)[0]
        fps_part, mode = body.split("|", 1)
        return int(fps_part.strip().split()[0]), mode.strip()
    except (IndexError, ValueError):
        return None, None


def presentmon_sample(exe: str, seconds: int) -> dict:
    tag = os.path.splitext(exe)[0].replace("DK4HD_", "")
    out = os.path.join(OUTDIR, f"fast60_pm_{tag}.csv")
    if os.path.exists(out):
        os.remove(out)
    if not os.path.exists(PRESENTMON):
        return {"error": "PresentMon not found"}
    try:
        r = subprocess.run(
            [PRESENTMON, "--process_name", exe, "--output_file", out,
             "--timed", str(seconds), "--terminate_after_timed",
             "--v1_metrics", "--no_console_stats"],
            capture_output=True, text=True, timeout=seconds + 20)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    if r.returncode != 0:
        return {"error": f"PresentMon exit {r.returncode}"}
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    ms = [float(row["msBetweenPresents"]) for row in rows if row["msBetweenPresents"]]
    if not ms:
        return {"error": "no PresentMon rows"}
    ms.sort()
    n = len(ms)
    return {
        "frames": n,
        "fps": round(1000.0 / statistics.mean(ms), 1),
        "p50_ms": round(ms[n // 2], 2),
        "p95_ms": round(ms[int(n * 0.95)], 2),
        "max_ms": round(max(ms), 2),
    }


def test_exe(exe: str, do_presentmon: bool, timeout: float, warmup: float,
             pm_seconds: int, assert_perf: bool) -> dict:
    tag = os.path.splitext(exe)[0].replace("DK4HD_", "")
    log_path = os.path.join(GAME, "DK4HD_Fix.log")
    try:
        os.remove(log_path)
    except OSError:
        pass

    result = {"exe": exe, "tag": tag, "ok": False, "checks": {}}
    proc = subprocess.Popen([os.path.join(GAME, exe)], cwd=GAME)
    result["pid"] = proc.pid
    try:
        hwnd = wait_window(proc.pid, timeout)
        if not hwnd:
            result["error"] = "game window not found"
            return result
        result["hwnd"] = hex(hwnd)
        focus(hwnd)
        time.sleep(3.0)

        t0 = title(hwnd)
        fps0, mode0 = parse_title_mode(t0)
        log = read_log(log_path)
        checks = result["checks"]
        checks["log_60fps_1"] = "60fps=1" in log
        checks["log_cpuidle_1"] = "CpuIdle=1" in log
        checks["fast60_enabled"] = "fast60 enabled at startup" in log
        checks["present_hook"] = "dxgi present hook: 0" in log
        checks["key_guard"] = "fast60 key guard: 0" in log
        checks["peek_hook"] = "hook PeekMessageA: 0" in log
        checks["frame_limiter_patched"] = "frame limiter: >33 -> >=33" in log
        checks["frame_limiter_hook"] = "frame limiter elapsed hook: 0" in log
        checks["title_fast60"] = mode0 is not None and "Fast60" in mode0
        checks["title_fps"] = fps0 is not None
        checks["focus_start"] = foreground_is(hwnd)

        result["title_start"] = t0
        result["fps_start"] = fps0

        if do_presentmon and proc.poll() is None:
            # Let the menu settle before sampling; a cold first second can
            # include one-off resource uploads and would skew the average.
            time.sleep(warmup)
            t3 = title(hwnd)
            _, mode3 = parse_title_mode(t3)
            checks["title_steady_fast60"] = mode3 is not None and "Fast60" in mode3
            result["title_steady"] = t3
            pm = presentmon_sample(exe, pm_seconds)
            result["presentmon"] = pm
            if assert_perf:
                if "error" in pm:
                    checks["presentmon_sample"] = False
                else:
                    checks["fast60_p50_<=16.9ms"] = pm["p50_ms"] <= FAST60_P50_MAX_MS
                    checks["fast60_p95_<=17.5ms"] = pm["p95_ms"] <= FAST60_P95_MAX_MS
                    checks["fast60_fps_>=59.5"] = pm["fps"] >= FAST60_MIN_FPS
            else:
                result["presentmon_recorded_only"] = True

        # Performance sample is taken before F10/focus changes so a one-off
        # DWM/driver hitch from window z-order changes is not counted as
        # steady-state menu performance.
        # F10: Fast60 -> 30Hz
        focus(hwnd)
        press_f10()
        t1, mode1 = wait_mode(hwnd, lambda m: m == "30Hz")
        checks["f10_to_30"] = mode1 == "30Hz"
        checks["focus_after_f10"] = foreground_is(hwnd)

        # F10: 30Hz -> Fast60
        focus(hwnd)
        press_f10()
        t2, mode2 = wait_mode(hwnd, lambda m: m is not None and "Fast60" in m)
        checks["f10_back_fast60"] = mode2 is not None and "Fast60" in mode2
        checks["focus_after_f10_back"] = foreground_is(hwnd)

        result["title_30hz"] = t1
        result["title_fast60"] = t2
        result["ok"] = all(checks.values())
        if not result["ok"]:
            result["log_tail"] = "\n".join(log.splitlines()[-25:])
        return result
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", action="append", choices=ALL_EXES,
                    help="test only this exe (repeatable)")
    ap.add_argument("--no-presentmon", action="store_true")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--warmup", type=float, default=8.0,
                    help="seconds to wait before the PresentMon sample")
    ap.add_argument("--presentmon-seconds", type=int, default=10)
    ap.add_argument("--game-dir", default=None,
                    help="game root (default: DK4HD_GAME or E:\\Games\\DK4HD)")
    ap.add_argument("--presentmon", default=None,
                    help="PresentMon executable (default: auto-download 2.6.0)")
    args = ap.parse_args()

    global GAME, PRESENTMON
    if args.game_dir:
        GAME = os.path.abspath(args.game_dir)
    if not os.path.isdir(GAME):
        print(f"missing game directory: {GAME}")
        return 2
    if not args.no_presentmon:
        if args.presentmon:
            PRESENTMON = args.presentmon
        elif not os.path.exists(PRESENTMON):
            try:
                from measure_frames import ensure_presentmon
                PRESENTMON = ensure_presentmon("")
            except Exception as e:  # noqa: BLE001
                print(f"warning: PresentMon not available: {e}")

    exes = args.exe or ALL_EXES
    ini = os.path.join(GAME, "DK4HD_Fix.ini")
    log = os.path.join(GAME, "DK4HD_Fix.log")
    results = []
    with backup_files([ini, log]):
        write_fast60_ini(ini)
        for idx, exe in enumerate(exes):
            if not os.path.exists(os.path.join(GAME, exe)):
                results.append({"exe": exe, "ok": False, "error": "exe not found"})
                print(f"{exe}: MISSING")
                continue
            print(f"=== {exe} ===", flush=True)
            r = test_exe(exe, not args.no_presentmon, args.timeout, args.warmup,
                         args.presentmon_seconds, assert_perf=(idx == 0))
            results.append(r)
            print(json.dumps(r, ensure_ascii=False, indent=2), flush=True)

        summary = os.path.join(OUTDIR, "fast60_results.json")
        with open(summary, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"summary: {summary}")
    return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
