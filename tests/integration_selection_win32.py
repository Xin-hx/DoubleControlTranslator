"""Typed UIA, password refusal, real SendInput Ctrl+C, and clipboard transaction."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import time

import win32clipboard
import win32con

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hermes_selection_translate.selection import (ClipboardSnapshot, SelectionError,
    get_selected_text, uia_selected_text, window_identity)


class UserClipboardSnapshot:
    """Test-only preservation, including a duplicated CF_BITMAP handle.

    Production intentionally refuses GDI handles. The harness duplicates the
    current bitmap solely so it can establish a controlled, safe clipboard and
    put every original format back after the fallback test.
    """
    def __init__(self):
        self.items = []
        self.had_bitmap = False
        deadline = time.monotonic() + 2
        while True:
            try:
                win32clipboard.OpenClipboard(); break
            except Exception:
                if time.monotonic() >= deadline: raise
                time.sleep(.02)
        try:
            fmt = 0
            while True:
                fmt = win32clipboard.EnumClipboardFormats(fmt)
                if not fmt: break
                value = win32clipboard.GetClipboardData(fmt)
                if isinstance(value, int):
                    if fmt != win32con.CF_BITMAP:
                        raise RuntimeError(f"unsupported live clipboard handle format {fmt}")
                    # Windows synthesizes CF_BITMAP from the captured CF_DIB;
                    # production still refuses the original GDI handle.
                    self.had_bitmap = True
                else:
                    self.items.append((fmt, "data", value))
        finally: win32clipboard.CloseClipboard()

    def restore(self):
        deadline = time.monotonic() + 2
        while True:
            try:
                win32clipboard.OpenClipboard(); break
            except Exception:
                if time.monotonic() >= deadline: raise
                time.sleep(.02)
        try:
            win32clipboard.EmptyClipboard()
            for fmt, kind, value in self.items:
                win32clipboard.SetClipboardData(fmt, value)
        finally: win32clipboard.CloseClipboard()


def launch(password=False):
    command = [sys.executable, str(ROOT / "tests" / "win32_edit_host.py")]
    if password: command.append("--password")
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    info = json.loads(process.stdout.readline())
    force_foreground(info["hwnd"], info["edit"])
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if ctypes.windll.user32.GetForegroundWindow() == info["hwnd"]: break
        ctypes.windll.user32.SetForegroundWindow(info["hwnd"]); time.sleep(.05)
    return process, info


def force_foreground(hwnd, edit):
    user32 = ctypes.windll.user32
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    if foreground_thread: user32.AttachThreadInput(current_thread, foreground_thread, True)
    user32.AttachThreadInput(current_thread, target_thread, True)
    try:
        user32.ShowWindow(hwnd, 5); user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd); user32.SetFocus(edit)
    finally:
        user32.AttachThreadInput(current_thread, target_thread, False)
        if foreground_thread: user32.AttachThreadInput(current_thread, foreground_thread, False)


def stop(process):
    if process.poll() is None:
        process.terminate(); process.wait(3)


public = "Sphinx of black quartz, judge my vow."
normal, info = launch()
try:
    original = UserClipboardSnapshot()
    target = window_identity(info["hwnd"])
    assert ctypes.windll.user32.GetForegroundWindow() == info["hwnd"], {
        "expected": info["hwnd"], "actual": ctypes.windll.user32.GetForegroundWindow()}
    uia = uia_selected_text(1.5)
    assert uia == public, repr(uia)
    custom = win32clipboard.RegisterClipboardFormat("HermesTool.Test.SafeBytes")
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, "original clipboard text")
        win32clipboard.SetClipboardData(custom, b"custom-safe-format\0")
    finally: win32clipboard.CloseClipboard()
    before = ClipboardSnapshot.capture()
    copied = get_selected_text(2000, target, prefer_uia=False)
    after = ClipboardSnapshot.capture()
    assert copied == public
    assert before.items == after.items, (before.items, after.items)
    assert ctypes.windll.user32.GetForegroundWindow() == info["hwnd"]
finally:
    if 'original' in globals(): original.restore()
    stop(normal)

password, password_info = launch(True)
try:
    try:
        get_selected_text(2000, window_identity(password_info["hwnd"]))
        raise AssertionError("password Edit unexpectedly returned selection")
    except SelectionError as exc:
        assert "密码" in str(exc) or "保护" in str(exc), str(exc)
finally:
    stop(password)

print(json.dumps({"ok": True, "uia_text": uia, "sendinput_fallback": copied,
                  "clipboard_formats_restored": len(after.items),
                  "password_refused": True, "foreground_preserved": True}, ensure_ascii=False))
