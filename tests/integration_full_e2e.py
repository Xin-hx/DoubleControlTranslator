"""Marker-gated hook -> typed selection -> real Qwen stream -> native card."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk

import win32clipboard

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from double_ctrl_translator.hook import E2E_INPUT_MARKER, KeyboardHook
from double_ctrl_translator.selection import INPUT, KEYBDINPUT
from double_ctrl_translator.config import Config
from double_ctrl_translator.worker import Worker
from model_harness import TemporaryLlama


def focus(hwnd, edit):
    user32 = ctypes.windll.user32; current = ctypes.windll.kernel32.GetCurrentThreadId()
    foreground = user32.GetForegroundWindow()
    old_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    if old_thread: user32.AttachThreadInput(current, old_thread, True)
    user32.AttachThreadInput(current, target_thread, True)
    try:
        user32.BringWindowToTop(hwnd); user32.SetForegroundWindow(hwnd); user32.SetFocus(edit)
    finally:
        user32.AttachThreadInput(current, target_thread, False)
        if old_thread: user32.AttachThreadInput(current, old_thread, False)


def send_tap(marker):
    def key(vk, flags=0): return INPUT(type=1, ki=KEYBDINPUT(vk, 0, flags, 0, marker))
    events = (INPUT * 2)(key(0x11), key(0x11, 2))
    user32 = ctypes.windll.user32
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    assert user32.SendInput(2, events, ctypes.sizeof(INPUT)) == 2


host = subprocess.Popen([sys.executable, str(ROOT / "tests" / "win32_edit_host.py")],
                        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
info = json.loads(host.stdout.readline()); focus(info["hwnd"], info["edit"])
root = None
events = queue.Queue(maxsize=1)

# Production boundary: even the project marker is ignored unless the explicit
# test-only constructor flag is present.
production_hook = KeyboardHook(350, events)
production_hook.start(); send_tap(E2E_INPUT_MARKER); time.sleep(.08); send_tap(E2E_INPUT_MARKER); time.sleep(.2)
assert events.empty(), "production hook accepted injected input"
production_hook.stop()

worker = Worker(Config(), 0, 0, allow_test_marker=True)
hook = worker.hook
sequence_before = int(win32clipboard.GetClipboardSequenceNumber())
try:
    # Settle Tk's initial withdraw and load the test model before capturing
    # the foreground identity, matching a ready production worker.
    worker.root.update()
    with TemporaryLlama() as llama:
        with worker.endpoint_lock:
            worker.endpoint = {**llama.endpoint, "model": "Qwen3.5-4B-Q4_K_M.gguf"}
        hook.start(); focus(info["hwnd"], info["edit"])
        send_tap(E2E_INPUT_MARKER); time.sleep(.09); send_tap(E2E_INPUT_MARKER)
        x, y, target = worker.triggers.get(timeout=3)
        foreground_before_card = ctypes.windll.user32.GetForegroundWindow()
        assert foreground_before_card == info["hwnd"]
        started = time.perf_counter()
        worker.translate(x, y, target)
        def finish_when_terminal():
            if worker.card.text or worker.last_error:
                worker.root.quit()
            else:
                worker.root.after(20, finish_when_terminal)
        worker.root.after(20, finish_when_terminal)
        worker.root.after(90000, worker.root.quit)
        worker.root.mainloop()
        translated = worker.card.text
        elapsed = time.perf_counter() - started
    foreground_after_card = ctypes.windll.user32.GetForegroundWindow()
    assert worker.card.visible()
    assert any("\u4e00" <= char <= "\u9fff" for char in translated), worker.last_error
    assert foreground_before_card == foreground_after_card == info["hwnd"]
    assert int(win32clipboard.GetClipboardSequenceNumber()) == sequence_before
    worker.card.close(); worker.root.update(); assert not worker.card.visible()
    print(json.dumps({"ok": True, "production_injection_ignored": True,
                      "test_marker_hook_triggered": True, "actual_worker_translate": True,
                      "result_is_chinese": True, "translate_s": round(elapsed, 3),
                      "card_visible_then_closed": True,
                      "foreground_preserved": True, "clipboard_sequence_unchanged": True,
                      "model_process_cleaned": True}, ensure_ascii=False))
finally:
    try: worker.stop()
    except tk.TclError: pass
    if host.poll() is None: host.terminate(); host.wait(3)
