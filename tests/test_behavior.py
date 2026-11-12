import io
import json
import os
import threading
import unittest
from unittest.mock import patch

from hermes_selection_translate.gesture import DoubleCtrlDetector
from hermes_selection_translate.model import TranslationError, _validate_loopback, stream_translate


class _FakeOpener:
    def __init__(self, body: bytes): self.body = body
    def open(self, request, timeout): return io.BytesIO(self.body)


class GestureTests(unittest.TestCase):
    def tap(self, detector, start):
        self.assertFalse(detector.feed(0x11, True, start))
        return detector.feed(0x11, False, start + 20)

    def test_two_complete_ctrl_taps_trigger(self):
        detector = DoubleCtrlDetector(350)
        self.assertFalse(self.tap(detector, 0))
        self.assertTrue(self.tap(detector, 200))

    def test_ctrl_a_ctrl_does_not_trigger(self):
        detector = DoubleCtrlDetector()
        self.tap(detector, 0)
        detector.feed(ord("A"), True, 30)
        detector.feed(ord("A"), False, 40)
        self.assertFalse(self.tap(detector, 100))

    def test_shift_held_ctrl_is_not_clean(self):
        detector = DoubleCtrlDetector()
        detector.feed(0x11, True, 0, other_modifier_down=True)
        detector.feed(0x11, False, 10)
        self.assertFalse(self.tap(detector, 100))

    def test_repeat_combo_and_untrusted_injection_never_trigger(self):
        detector = DoubleCtrlDetector()
        detector.feed(0x11, True, 0)
        detector.feed(0x11, True, 5)
        detector.feed(ord("C"), True, 6)
        detector.feed(0x11, False, 8)
        self.assertFalse(self.tap(detector, 30))
        detector.feed(0x11, True, 50, injected=True)
        self.assertFalse(detector.feed(0x11, False, 60, injected=True))

    def test_slow_taps_do_not_trigger(self):
        detector = DoubleCtrlDetector(350)
        self.tap(detector, 0)
        self.assertFalse(self.tap(detector, 500))


class ModelTests(unittest.TestCase):
    def test_only_ip_literal_loopback_is_allowed(self):
        for url in ("https://127.0.0.1:1/v1", "http://localhost:1/v1",
                    "http://example.com/v1", "http://127.0.0.1@example.com/v1"):
            with self.subTest(url=url), self.assertRaises(TranslationError):
                _validate_loopback(url)
        _validate_loopback("http://127.0.0.1:1/v1")
        _validate_loopback("http://[::1]:1/v1")

    def test_sse_stream(self):
        body = ('data: {"choices":[{"delta":{"content":"你"},"finish_reason":null}]}\n\n'
                'data: {"choices":[{"delta":{"content":"好"},"finish_reason":"stop"}]}\n\n'
                'data: [DONE]\n').encode()
        seen = []
        with patch("hermes_selection_translate.model._opener", return_value=_FakeOpener(body)):
            result = stream_translate("hello", {"base_url": "http://127.0.0.1:1/v1", "api_key": "secret"},
                                      "m", seen.append, threading.Event())
        self.assertEqual(result, "你好")
        self.assertEqual(seen, ["你", "你好"])

    def test_finish_length_is_failure(self):
        body = (b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"length"}]}\n\n'
                b'data: [DONE]\n')
        with patch("hermes_selection_translate.model._opener", return_value=_FakeOpener(body)):
            with self.assertRaisesRegex(TranslationError, "不完整"):
                stream_translate("x", {"base_url": "http://127.0.0.1:1/v1"},
                                 "m", lambda _: None, threading.Event())

    def test_missing_done_is_failure(self):
        body = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n'
        with patch("hermes_selection_translate.model._opener", return_value=_FakeOpener(body)):
            with self.assertRaisesRegex(TranslationError, "意外中断"):
                stream_translate("x", {"base_url": "http://127.0.0.1:1/v1"},
                                 "m", lambda _: None, threading.Event())

    def test_total_generation_deadline_is_enforced(self):
        body = b'data: {"choices":[{"delta":{"content":"late"}}]}\n'
        with patch("hermes_selection_translate.model._opener", return_value=_FakeOpener(body)):
            with self.assertRaisesRegex(TranslationError, "总时限"):
                stream_translate("x", {"base_url": "http://127.0.0.1:1/v1"},
                                 "m", lambda _: None, threading.Event(), total_timeout=0)


if __name__ == "__main__": unittest.main()
