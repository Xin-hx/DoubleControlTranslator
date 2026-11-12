"""Isolated ordinary Win32 Edit host used only by project integration tests."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os


parser = argparse.ArgumentParser()
parser.add_argument("--password", action="store_true")
args = parser.parse_args()
user32 = ctypes.windll.user32
WS_OVERLAPPEDWINDOW, WS_VISIBLE, WS_CHILD = 0x00CF0000, 0x10000000, 0x40000000
ES_MULTILINE, ES_PASSWORD = 0x0004, 0x0020
parent = user32.CreateWindowExW(0, "STATIC", "HermesTool isolated selection test",
                                WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                120, 120, 640, 220, None, None, None, None)
style = WS_CHILD | WS_VISIBLE | (ES_PASSWORD if args.password else ES_MULTILINE)
sentence = "Sphinx of black quartz, judge my vow."
edit = user32.CreateWindowExW(0, "EDIT", sentence, style,
                              18, 24, 580, 110, parent, None, None, None)
if not parent or not edit:
    raise SystemExit(2)
user32.SendMessageW(edit, 0x00B1, 0, len(sentence))  # EM_SETSEL
user32.SetForegroundWindow(parent)
user32.SetFocus(edit)
print(json.dumps({"hwnd": int(parent), "edit": int(edit), "pid": os.getpid()}), flush=True)
message = wintypes.MSG()
while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
    user32.TranslateMessage(ctypes.byref(message)); user32.DispatchMessageW(ctypes.byref(message))
