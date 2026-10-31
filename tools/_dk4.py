"""Shared Windows helpers for the DK4HD acceptance scripts (Python 3.12).

The scripts are intentionally dependency-light: ctypes for the window/input
calls, frida only where explicitly needed. All paths are Windows paths.
"""

import ctypes
import ctypes.wintypes as wt
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager

WINDOW_CLASS = "KT_DK4_PK_HD_WINDOW_NAME"
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def _set_dpi_awareness():
    """Make ClientToScreen/SetCursorPos use physical pixels on scaled displays."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


_set_dpi_awareness()


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def process_names():
    """Return lower-case image names of all processes."""
    names = set()
    kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    kernel32.Process32First.argtypes = [wt.HANDLE, ctypes.c_void_p]
    kernel32.Process32Next.argtypes = [wt.HANDLE, ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == wt.HANDLE(-1).value:
        return names
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32First(snap, ctypes.byref(entry))
        while ok:
            names.add(entry.szExeFile.decode("latin1").lower())
            ok = kernel32.Process32Next(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return names


def running_game_processes():
    """Image names of already running DK4HD builds."""
    return sorted(n for n in process_names() if n.startswith("dk4hd"))


def ensure_no_game_running():
    """Print a clear failure and return False if a game instance is already up."""
    busy = running_game_processes()
    if busy:
        print(f"FAIL: game already running ({', '.join(busy)}); close it first")
        return False
    return True


def wait_for_file(path, timeout=10.0):
    """Wait until *path* exists; return True if it appeared."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.1)
    return os.path.exists(path)


def find_window(pid, timeout=30.0):
    """Return the game's main HWND for *pid*, or None."""
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
            if cls == WINDOW_CLASS:
                return hwnd
        time.sleep(0.25)
    return None


def focus_window(hwnd):
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


def client_center(hwnd):
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    pt = wt.POINT((rect.right - rect.left) // 2, (rect.bottom - rect.top) // 2)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def start_game(exe_path, game_dir):
    return subprocess.Popen([exe_path], cwd=game_dir)


def stop_game(proc):
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
    except Exception:
        pass


def read_text(path, timeout=2.0):
    """Read a text file, retrying briefly while the game flushes it."""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    last = f.read()
                if last:
                    return last
        except OSError:
            pass
        time.sleep(0.2)
    return last


def read_ini(path):
    """Return a dict of the integer values in the [Fix] section."""
    values = {}
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except OSError:
        return values
    for key, value in re.findall(r"(?mi)^\s*([A-Za-z0-9_]+)\s*=\s*(\d+)\s*$", text):
        values[key.lower()] = int(value)
    return values


def write_test_ini(path, frame=1, cpu=1, latch=1, release=1, skip=0, log=0, fps=0,
                   enum=1):
    text = (
        "; test INI generated by the DK4HD acceptance script\n"
        "[Fix]\n"
        f"FramePacing={frame}\n"
        f"CpuIdle={cpu}\n"
        f"ClickLatch={latch}\n"
        f"ClickRelease={release}\n"
        f"SkipIntro={skip}\n"
        f"Log={log}\n"
        f"60fps={fps}\n"
        f"EnumCache={enum}\n"
    )
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(text)


@contextmanager
def backup_files(paths):
    """Back up existing files and restore them (or remove new ones) afterwards."""
    backups = {}
    for path in paths:
        if os.path.exists(path):
            fd, tmp = tempfile.mkstemp(prefix="dk4_bak_", suffix=".tmp")
            os.close(fd)
            shutil.copy2(path, tmp)
            backups[path] = tmp
        else:
            backups[path] = None
    try:
        yield
    finally:
        for path, tmp in backups.items():
            try:
                if tmp:
                    shutil.copy2(tmp, path)
                    os.remove(tmp)
                elif os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
