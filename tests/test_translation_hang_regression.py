import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hermes_selection_translate.selection import SelectionError, WindowIdentity
from hermes_selection_translate.worker import Worker
from hermes_selection_translate.model import TranslationError, warmup


class _Root:
    def after(self, _delay, callback):
        callback()


class _Card:
    def __init__(self):
        self.updates = []
        self.done = threading.Event()

    def show(self, *_):
        self.updates.append(("读取选区", "progress"))

    def update(self, text, state="success"):
        self.updates.append((text, state))
        if state in {"success", "错误"}:
            self.done.set()


def _worker():
    worker = Worker.__new__(Worker)
    worker.config = SimpleNamespace(max_chars=1000, model="Qwen3.5-4B-Q4_K_M.gguf",
                                    read_timeout_s=1, connect_timeout_s=1, total_timeout_s=2)
    worker.request_lock = threading.Lock()
    worker.endpoint_lock = threading.Lock()
    worker.endpoint = {"base_url": "http://127.0.0.1:1/v1", "model": "Qwen3.5-4B-Q4_K_M"}
    worker.cancel = threading.Event()
    worker.generation = 0
    worker.selection_failures = worker.translation_failures = 0
    worker.last_error = None
    worker.root = _Root()
    worker.card = _Card()
    worker.stopped = False
    return worker


class WorkerProductionPathTests(unittest.TestCase):
    def test_int_target_reaches_terminal_success(self):
        worker = _worker()
        identity = WindowIdentity(7, 8, 9.0)
        with patch("hermes_selection_translate.worker.window_identity", return_value=identity), \
             patch("hermes_selection_translate.worker.get_selected_text", return_value="public sample") as selected, \
             patch("hermes_selection_translate.worker.stream_translate", return_value="公开译文"):
            worker.translate(1, 2, 7)
            self.assertTrue(worker.card.done.wait(1), worker.card.updates)
        selected.assert_called_once()
        self.assertIs(selected.call_args.args[1], identity)
        self.assertEqual(worker.card.updates[-1], ("公开译文", "success"))

    def test_identity_target_reaches_terminal_success(self):
        worker = _worker()
        identity = WindowIdentity(7, 8, 9.0)
        with patch("hermes_selection_translate.worker.get_selected_text", return_value="public sample") as selected, \
             patch("hermes_selection_translate.worker.stream_translate", return_value="公开译文"):
            worker.translate(1, 2, identity)
            self.assertTrue(worker.card.done.wait(1), worker.card.updates)
        self.assertIs(selected.call_args.args[1], identity)

    def test_selection_and_unexpected_errors_are_terminal_and_sanitized(self):
        for error, expected in [(SelectionError("UIA failed"), "UIA failed"),
                                (UnboundLocalError("private selected text"), "处理失败（UnboundLocalError）")]:
            worker = _worker()
            with patch("hermes_selection_translate.worker.get_selected_text", side_effect=error):
                worker.translate(1, 2, WindowIdentity(7, 8, 9.0))
                self.assertTrue(worker.card.done.wait(1), worker.card.updates)
            self.assertEqual(worker.card.updates[-1], (expected, "错误"))
            if isinstance(error, UnboundLocalError):
                self.assertNotIn("private", worker.last_error)

    def test_cancel_prevents_old_result_overwriting_card(self):
        worker = _worker()
        release = threading.Event()
        def translate(*_args):
            release.wait(1)
            return "late"
        with patch("hermes_selection_translate.worker.stream_translate", side_effect=translate):
            worker.translate(1, 2, None, "public sample")
            worker._cancel()
            release.set()
            time.sleep(.05)
        self.assertNotIn(("late", "success"), worker.card.updates)


class WarmupTests(unittest.TestCase):
    def test_warmup_uses_public_prompt_real_transport_and_reports_first_token(self):
        cancel = threading.Event()
        seen = {}
        def transport(text, endpoint, model, on_delta, passed_cancel, *args, **kwargs):
            seen.update(text=text, endpoint=endpoint, model=model, cancel=passed_cancel,
                        timeout=args[2], max_chars=kwargs["max_result_chars"])
            on_delta("你好")
            return "你好"
        endpoint = {"base_url": "http://127.0.0.1:1/v1"}
        with patch("hermes_selection_translate.model.stream_translate", side_effect=transport):
            timing = warmup(endpoint, "Qwen3.5-4B-Q4_K_M", cancel, 4)
        self.assertEqual(seen["text"], "Hello.")
        self.assertIs(seen["cancel"], cancel)
        self.assertEqual(seen["timeout"], 4)
        self.assertIn("warmup_first_token_ms", timing)

    def test_warmup_failure_and_cancellation_are_not_ready(self):
        for cancel_first in (False, True):
            cancel = threading.Event()
            if cancel_first:
                cancel.set()
            with patch("hermes_selection_translate.model.stream_translate",
                       side_effect=TranslationError("翻译已取消" if cancel_first else "timeout")):
                with self.assertRaises(TranslationError):
                    warmup({"base_url": "http://127.0.0.1:1/v1"}, "Qwen3.5-4B-Q4_K_M", cancel, 1)


if __name__ == "__main__":
    unittest.main()
