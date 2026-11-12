"""Real Tk/Win32 card: no focus steal, monitor work-area bounds, scroll, cleanup."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import tkinter as tk
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hermes_selection_translate.card import MONITORINFO, TranslationCard

host = subprocess.Popen([sys.executable, str(ROOT / "tests" / "win32_edit_host.py")],
                        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
info = json.loads(host.stdout.readline())
root = tk.Tk(); root.withdraw(); root.update()
user32 = ctypes.windll.user32
current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
foreground = user32.GetForegroundWindow()
foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
target_thread = user32.GetWindowThreadProcessId(info["hwnd"], None)
if foreground_thread: user32.AttachThreadInput(current_thread, foreground_thread, True)
user32.AttachThreadInput(current_thread, target_thread, True)
try:
    user32.BringWindowToTop(info["hwnd"]); user32.SetForegroundWindow(info["hwnd"]); user32.SetFocus(info["edit"])
finally:
    user32.AttachThreadInput(current_thread, target_thread, False)
    if foreground_thread: user32.AttachThreadInput(current_thread, foreground_thread, False)
time.sleep(.1)
before = ctypes.windll.user32.GetForegroundWindow()
foreground_trace = {"before_card": int(before)}
card = TranslationCard(root, lambda: None)
foreground_trace["after_construct"] = int(user32.GetForegroundWindow())
card.show(-100, 80)
foreground_trace["after_show"] = int(user32.GetForegroundWindow())
card.update("长译文" * 10000)
foreground_trace["after_content_update"] = int(user32.GetForegroundWindow())
root.update()
after = ctypes.windll.user32.GetForegroundWindow()
foreground_trace["after_root_update"] = int(after)
hwnd = card.hwnd
style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
rect = wintypes.RECT(); ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
point = wintypes.POINT(rect.left, rect.top)
monitor = ctypes.windll.user32.MonitorFromPoint(point, 2)
mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO)); ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(mi))
bounded = (rect.left >= mi.rcWork.left and rect.top >= mi.rcWork.top and
           rect.right <= mi.rcWork.right and rect.bottom <= mi.rcWork.bottom)
result = {"ok": bool(ctypes.windll.user32.IsWindowVisible(hwnd)),
          "no_activate_style": bool(style & 0x08000000),
          "foreground_unchanged": before == after == info["hwnd"],
          "work_area_bounded": bounded,
          "scrollable": card.output.cget("yscrollcommand") != "",
          "height": rect.bottom - rect.top,
          "expected_foreground": info["hwnd"],
          "foreground_trace": foreground_trace}
card.close(); root.update(); result["closed"] = not bool(ctypes.windll.user32.IsWindow(hwnd))
root.destroy()
if host.poll() is None: host.terminate(); host.wait(3)
print(json.dumps(result, ensure_ascii=False))
if not all(result[k] for k in ("ok", "no_activate_style", "foreground_unchanged",
                                "work_area_bounded", "scrollable", "closed")):
    raise SystemExit(1)
