import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from double_ctrl_translator.manager import WorkerManager


class _Process:
    def __init__(self): self.stdin = io.StringIO()
    def poll(self): return None


class ManagerEndpointRefreshTests(unittest.TestCase):
    def test_same_source_changed_endpoint_and_key_are_forwarded_without_status_leak(self):
        manager = WorkerManager(Path.cwd())
        manager.process = _Process()
        endpoints = [({"base_url": "http://127.0.0.1:1/v1", "api_key": "first-secret"}, "shared"),
                     ({"base_url": "http://127.0.0.1:2/v1", "api_key": "second-secret"}, "shared")]
        with patch.object(manager, "_worker_running", return_value=True), \
             patch.object(manager, "_select_endpoint_locked", side_effect=endpoints):
            first = manager.status(); second = manager.status()
        frames = [line for line in manager.process.stdin.getvalue().splitlines()
                  if json.loads(line).get("op") == "endpoint"]
        self.assertEqual(len(frames), 2)
        self.assertNotIn("secret", str(first) + str(second))
        self.assertIn("127.0.0.1:2", frames[1])


if __name__ == "__main__": unittest.main()
