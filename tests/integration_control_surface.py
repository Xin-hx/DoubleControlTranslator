"""Real Tk taskbar control window, status transition, button, and project-only screenshot."""
from __future__ import annotations

import json
import ctypes
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

import psutil
import win32con
import win32gui
import win32process
import win32ui


ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT = ROOT / "runtime" / "control-surface.bmp"
process = subprocess.Popen(
    [sys.executable, "-m", "hermes_selection_translate.control_surface", "--test-mode"],
    cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), shell=False,
)
created = psutil.Process(process.pid).create_time()


def find_window():
    found = []
    owner_pids = {process.pid}
    try:
        owner_pids.update(child.pid for child in psutil.Process(process.pid).children(recursive=True))
    except psutil.Error:
        pass
    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] in owner_pids:
            found.append(hwnd)
    win32gui.EnumWindows(visit, None)
    return found[0] if found else None


def capture(hwnd):
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    window_dc = win32gui.GetWindowDC(hwnd)
    source = win32ui.CreateDCFromHandle(window_dc)
    target = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, width, height)
    target.SelectObject(bitmap)
    try:
        ctypes.windll.user32.PrintWindow(hwnd, target.GetSafeHdc(), 2)
        SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
        bitmap.SaveBitmapFile(target, str(SCREENSHOT))
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        target.DeleteDC(); source.DeleteDC(); win32gui.ReleaseDC(hwnd, window_dc)


try:
    disabled = {"enabled": False, "state": "disabled", "model": "none",
                "model_readiness": "unloaded", "diagnostics": {"hook_healthy": False}}
    process.stdin.write(json.dumps(disabled) + "\n"); process.stdin.flush()
    deadline = time.monotonic() + 8
    hwnd = None
    while time.monotonic() < deadline and not hwnd:
        hwnd = find_window()
        time.sleep(.05)
    assert hwnd, "native taskbar window did not become visible"
    assert "已关闭" in win32gui.GetWindowText(hwnd)

    action_lines = queue.Queue()
    threading.Thread(target=lambda: action_lines.put(process.stdout.readline()), daemon=True).start()
    action = json.loads(action_lines.get(timeout=4))
    assert action == {"action": "enable_and_load"}, action

    loaded = {"enabled": True, "state": "running", "model": "project",
              "model_readiness": "loaded", "worker_pid": process.pid,
              "worker_rss_bytes": psutil.Process(process.pid).memory_info().rss,
              "diagnostics": {"hook_healthy": True, "keyboard_events": 12,
                              "pure_ctrl_taps": 2, "double_ctrl_triggers": 1,
                              "selection_failures": 0, "translation_failures": 0}}
    process.stdin.write(json.dumps(loaded) + "\n"); process.stdin.flush()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and "就绪" not in win32gui.GetWindowText(hwnd):
        time.sleep(.05)
    assert "就绪" in win32gui.GetWindowText(hwnd), win32gui.GetWindowText(hwnd)
    capture(hwnd)
    assert SCREENSHOT.is_file() and SCREENSHOT.stat().st_size > 10_000
    print(json.dumps({"ok": True, "visible_taskbar_window": True,
                      "disabled_and_loaded_distinguished": True,
                      "primary_button_emitted_enable_and_load": True,
                      "screenshot": str(SCREENSHOT.relative_to(ROOT))}))
finally:
    try:
        process.stdin.close()
    except Exception:
        pass
    try:
        process.wait(5)
    except subprocess.TimeoutExpired:
        try:
            current = psutil.Process(process.pid)
            if abs(current.create_time() - created) < .01:
                current.terminate(); current.wait(3)
        except psutil.Error:
            pass
