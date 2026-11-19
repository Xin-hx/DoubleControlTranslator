"""Real-HWND metadata fixture; it never opens the user's clipboard."""
from __future__ import annotations

import ctypes
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from double_ctrl_translator.selection import _clipboard_window_matches, window_identity

user32 = ctypes.windll.user32
WS_OVERLAPPEDWINDOW, WS_VISIBLE = 0x00CF0000, 0x10000000
document = user32.CreateWindowExW(0, "STATIC", "Hermes fixture document",
                                  WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                  0, 0, 300, 100, None, None, None, None)
broker = user32.CreateWindowExW(0, "STATIC", "", 0,
                                0, 0, 0, 0, None, None, None, None)
other_document = user32.CreateWindowExW(0, "STATIC", "Hermes other document",
                                        WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                        0, 0, 300, 100, None, None, None, None)
if not document or not broker or not other_document:
    raise ctypes.WinError()
expected = window_identity(document)
print(json.dumps({
    "hidden_match": _clipboard_window_matches(expected, int(broker)),
    "visible_other_rejected": not _clipboard_window_matches(expected, int(other_document)),
    "root_differs": int(user32.GetAncestor(broker, 2) or broker) != int(document),
    "hidden": not bool(user32.IsWindowVisible(broker)),
}), flush=True)
