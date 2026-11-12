import ctypes
import threading
import unittest
from unittest.mock import Mock, patch

import win32con

from hermes_selection_translate.selection import (ClipboardSnapshot, INPUT, SelectionError, VARIANT,
                                                    WindowIdentity, clipboard_fallback,
                                                    get_selected_text, _clipboard_owner_matches,
                                                    _clipboard_window_matches,
                                                    _uia_pending)


class SelectionTests(unittest.TestCase):
    def setUp(self): self.target = WindowIdentity(1, 2, 3)

    def test_win64_input_layout_is_complete(self):
        self.assertEqual(ctypes.sizeof(INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        self.assertEqual(ctypes.sizeof(VARIANT), 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)

    def test_uia_wins_without_clipboard(self):
        with patch("hermes_selection_translate.selection.verify_foreground"), \
             patch("hermes_selection_translate.selection._uia_probe", return_value=("hello", True)), \
             patch("hermes_selection_translate.selection.clipboard_fallback") as fallback:
            self.assertEqual(get_selected_text(10, self.target), "hello")
            fallback.assert_not_called()

    def test_safe_empty_uia_falls_back_without_global_monkeypatch(self):
        with patch("hermes_selection_translate.selection.verify_foreground"), \
             patch("hermes_selection_translate.selection._uia_probe", return_value=(None, True)), \
             patch("hermes_selection_translate.selection.clipboard_fallback", return_value="world"):
            self.assertEqual(get_selected_text(10, self.target), "world")

    def test_uia_failure_is_fail_closed_for_password_safety(self):
        with patch("hermes_selection_translate.selection.verify_foreground"), \
             patch("hermes_selection_translate.selection._uia_probe",
                   side_effect=SelectionError("UI Automation 读取失败")), \
             patch("hermes_selection_translate.selection.clipboard_fallback") as fallback:
            with self.assertRaises(SelectionError): get_selected_text(10, self.target)
            fallback.assert_not_called()

    def test_cancel_checked_before_selection(self):
        cancel = threading.Event(); cancel.set()
        with patch("hermes_selection_translate.selection._uia_probe") as probe:
            with self.assertRaisesRegex(SelectionError, "取消"):
                get_selected_text(10, self.target, cancel)
            probe.assert_not_called()

    def test_cancel_after_uia_probe_is_checked_before_result_use(self):
        cancel = threading.Event()
        def finish_probe(*_, **__): cancel.set(); return ("late", True)
        with patch("hermes_selection_translate.selection.verify_foreground"), \
             patch("hermes_selection_translate.selection._uia_probe", side_effect=finish_probe):
            with self.assertRaisesRegex(SelectionError, "取消"):
                get_selected_text(10, self.target, cancel)

    def test_only_one_timed_out_uia_probe_can_be_pending(self):
        _uia_pending.acquire()
        try:
            with self.assertRaisesRegex(SelectionError, "仍在进行"):
                from hermes_selection_translate.selection import _uia_probe
                _uia_probe(.01)
        finally:
            _uia_pending.release()

    def test_limit_is_enforced_by_worker_entrypoint(self):
        with patch("hermes_selection_translate.selection.verify_foreground"), \
             patch("hermes_selection_translate.selection._uia_probe", return_value=("12345", True)):
            with self.assertRaises(SelectionError): get_selected_text(4, self.target)

    def test_external_clipboard_update_is_never_overwritten(self):
        snapshot = ClipboardSnapshot([(13, "old")], 10)
        with patch("hermes_selection_translate.selection.win32clipboard.GetClipboardSequenceNumber",
                   return_value=12), \
             patch("hermes_selection_translate.selection.win32clipboard.OpenClipboard") as opened:
            self.assertFalse(snapshot.restore_if_unchanged(11))
            opened.assert_not_called()

    def test_bitmap_handle_is_represented_by_safe_dib_and_terminal_sequence(self):
        formats = iter([win32con.CF_BITMAP, win32con.CF_DIB, 0,
                        win32con.CF_BITMAP, win32con.CF_DIB, 0])
        values = {win32con.CF_DIB: b"public-dib"}
        with patch("hermes_selection_translate.selection._open_clipboard"), \
             patch("hermes_selection_translate.selection.win32clipboard.CloseClipboard"), \
             patch("hermes_selection_translate.selection.win32clipboard.EnumClipboardFormats",
                   side_effect=lambda _: next(formats)), \
             patch("hermes_selection_translate.selection.win32clipboard.GetClipboardData",
                   side_effect=lambda fmt: values[fmt]) as get_data, \
             patch("hermes_selection_translate.selection.win32clipboard.GetClipboardSequenceNumber",
                   side_effect=[10, 11, 11, 11]):
            snapshot = ClipboardSnapshot.capture()
        self.assertEqual(snapshot.items, [(win32con.CF_DIB, b"public-dib")])
        self.assertEqual(snapshot.sequence, 11)
        self.assertEqual(get_data.call_count, 2)

    def test_bitmap_without_safe_dib_representation_fails_closed(self):
        formats = iter([win32con.CF_BITMAP, 0])
        with patch("hermes_selection_translate.selection._open_clipboard"), \
             patch("hermes_selection_translate.selection.win32clipboard.CloseClipboard"), \
             patch("hermes_selection_translate.selection.win32clipboard.EnumClipboardFormats",
                   side_effect=lambda _: next(formats)), \
             patch("hermes_selection_translate.selection.win32clipboard.GetClipboardData") as get_data:
            with self.assertRaisesRegex(SelectionError, "GDI"):
                ClipboardSnapshot.capture()
        get_data.assert_not_called()

    def test_unknown_opaque_handle_still_fails_closed(self):
        custom = 0xC123
        formats = iter([custom, 0])
        with patch("hermes_selection_translate.selection._open_clipboard"), \
             patch("hermes_selection_translate.selection.win32clipboard.CloseClipboard"), \
             patch("hermes_selection_translate.selection.win32clipboard.EnumClipboardFormats",
                   side_effect=lambda _: next(formats)), \
             patch("hermes_selection_translate.selection.win32clipboard.GetClipboardData",
                   return_value=12345):
            with self.assertRaisesRegex(SelectionError, "GDI"):
                ClipboardSnapshot.capture()

    def test_unattributed_sequence_change_is_not_read_or_restored(self):
        snapshot = Mock(sequence=10)
        user32 = Mock()
        user32.GetAsyncKeyState.return_value = 0
        with patch("hermes_selection_translate.selection.ctypes.windll.user32", user32), \
             patch("hermes_selection_translate.selection.verify_foreground"), \
             patch("hermes_selection_translate.selection.ClipboardSnapshot.capture", return_value=snapshot), \
             patch("hermes_selection_translate.selection._send_ctrl_c"), \
             patch("hermes_selection_translate.selection._clipboard_owner_matches", return_value=False), \
             patch("hermes_selection_translate.selection.win32clipboard.GetClipboardSequenceNumber", return_value=11):
            with self.assertRaisesRegex(SelectionError, "其他程序"):
                clipboard_fallback(self.target)
        snapshot.restore_if_unchanged.assert_called_once_with(None)

    def test_same_process_hidden_top_level_clipboard_owner_is_accepted(self):
        user32 = Mock()
        user32.GetClipboardOwner.return_value = 20
        user32.GetWindowThreadProcessId.side_effect = lambda hwnd, pid: setattr(pid._obj, "value", 2) or 8
        user32.GetAncestor.return_value = 20
        user32.IsWindow.return_value = True
        user32.IsWindowVisible.return_value = False
        with patch("hermes_selection_translate.selection.ctypes.windll.user32", user32), \
             patch("hermes_selection_translate.selection.window_identity",
                   return_value=WindowIdentity(20, 2, 3)):
            self.assertTrue(_clipboard_owner_matches(self.target))

    def test_unknown_other_process_reused_pid_and_visible_other_document_are_rejected(self):
        user32 = Mock()
        user32.IsWindow.return_value = True
        user32.GetAncestor.side_effect = lambda hwnd, _: hwnd
        user32.IsWindowVisible.return_value = False
        with patch("hermes_selection_translate.selection.ctypes.windll.user32", user32), \
             patch("hermes_selection_translate.selection.window_identity") as identity:
            self.assertFalse(_clipboard_window_matches(self.target, 0))
            identity.return_value = WindowIdentity(20, 99, 3)
            self.assertFalse(_clipboard_window_matches(self.target, 20))
            identity.return_value = WindowIdentity(20, 2, 999)
            self.assertFalse(_clipboard_window_matches(self.target, 20))
            identity.return_value = WindowIdentity(20, 2, 3)
            user32.IsWindowVisible.return_value = True
            self.assertFalse(_clipboard_window_matches(self.target, 20))

    def test_owner_switch_while_clipboard_is_open_is_not_read_or_overwritten(self):
        snapshot = Mock(sequence=10)
        user32 = Mock()
        user32.GetAsyncKeyState.return_value = 0
        with patch("hermes_selection_translate.selection.ctypes.windll.user32", user32), \
             patch("hermes_selection_translate.selection.verify_foreground"), \
             patch("hermes_selection_translate.selection.ClipboardSnapshot.capture", return_value=snapshot), \
             patch("hermes_selection_translate.selection._send_ctrl_c"), \
             patch("hermes_selection_translate.selection._open_clipboard"), \
             patch("hermes_selection_translate.selection.win32clipboard.CloseClipboard"), \
             patch("hermes_selection_translate.selection._clipboard_owner_matches",
                   side_effect=[True, False]), \
             patch("hermes_selection_translate.selection.win32clipboard.GetClipboardSequenceNumber",
                   side_effect=[11, 11]), \
             patch("hermes_selection_translate.selection.win32clipboard.GetClipboardData") as read:
            with self.assertRaisesRegex(SelectionError, "来源已变化"):
                clipboard_fallback(self.target)
            read.assert_not_called()
        snapshot.restore_if_unchanged.assert_called_once_with(None)


if __name__ == "__main__": unittest.main()
