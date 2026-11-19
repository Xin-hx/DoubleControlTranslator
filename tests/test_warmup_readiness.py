"""A failed startup inference must not be advertised as a ready translator."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from double_ctrl_translator.manager import WorkerManager
from double_ctrl_translator.model import TranslationError


class WarmupReadinessTests(unittest.TestCase):
    def test_owned_warmup_failure_returns_error_not_running(self):
        manager = WorkerManager(Path.cwd())
        manager.desktop = SimpleNamespace(alive=lambda: True)
        endpoint = {"base_url": "http://127.0.0.1:18435/v1", "api_key": "",
                    "model": "Qwen3.5-4B-Q4_K_M.gguf"}
        with patch.object(manager, "_worker_running", return_value=True), \
                patch.object(manager.model, "start", return_value=endpoint), \
                patch.object(manager.model, "running", return_value=True), \
                patch.object(manager, "_send_locked", return_value=True), \
                patch("double_ctrl_translator.manager.warmup",
                      side_effect=TranslationError("预热失败")):
            result = manager.start_model()
        self.assertEqual(result["state"], "error")
        self.assertEqual(result["error"], "预热失败")


if __name__ == "__main__":
    unittest.main()
