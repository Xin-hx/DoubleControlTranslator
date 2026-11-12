"""Native real-HWND regression without accessing the interactive clipboard."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
completed = subprocess.run(
    [sys.executable, str(ROOT / "tests" / "win32_hidden_clipboard_owner_host.py")],
    cwd=ROOT, capture_output=True, text=True, timeout=10, check=True)
result = json.loads(completed.stdout)
assert result == {"hidden_match": True, "visible_other_rejected": True,
                  "root_differs": True, "hidden": True}, result
print(json.dumps({"ok": True, **result}))
