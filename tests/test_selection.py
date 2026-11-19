import ctypes
import threading
import unittest
from unittest.mock import Mock, patch

import win32con
import pywintypes

from double_ctrl_translator.selection import (ClipboardSnapshot, INPUT, SelectionError, VARIANT,
                                                    WindowIdentity, clipboard_fallback,
                                                    get_selected_text, _clipboard_owner_matches,
                                                    _clipboard_window_matches,
                                                    _verify_uia_window,
                                                    _native_focused_window,
                                                    _uia_pending)


class SelectionTests(unittest.TestCase):
    def setUp(self): self.target = WindowIdentity(1, 2, 3)

    def test_win64_input_layout_is_complete(self):
        self.assertEqual(ctypes.sizeof(INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        self.assertEqual(ctypes.sizeof(VARIANT), 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)

    def test_uia_accepts_cross_process_child_only_in_original_window(self):
        user32 = Mock()
        child = WindowIdentity(20, 99, 4)
        with patch("double_ctrl_translator.selection.ctypes.windll.user32", user32), \
             patch("double_ctrl_translator.selection._property_int", side_effect=[99, 20] * 4), \
             patch("double_ctrl_translator.selection.window_identity") as identity:
            user32.GetAncestor.return_value = self.target.hwnd
            identity.side_effect = [self.target, child]
            _verify_uia_window(ctypes.c_void_p(1), self.target)
            user32.GetAncestor.return_value = 30
            with self.assertRaisesRegex(SelectionError, "窗口身份"):
                _verify_uia_window(ctypes.c_void_p(1), self.target)
            user32.GetAncestor.return_value = self.target.hwnd
            identity.side_effect = [WindowIdentity(1, 2, 999)]
            with self.assertRaisesRegex(SelectionError, "窗口身份"):
                _verify_uia_window(ctypes.c_void_p(1), self.target)
            identity.side_effect = [self.target, WindowIdentity(20, 100, 4)]
            with self.assertRaisesRegex(SelectionError, "进程身份"):
                _verify_uia_window(ctypes.c_void_p(1), self.target)

    def test_uia_without_native_handle_still_requires_matching_process(self):
        with patch("double_ctrl_translator.selection._property_int", side_effect=[99, 0]):
            with self.assertRaisesRegex(SelectionError, "不属于"):
                _verify_uia_window(ctypes.c_void_p(1), self.target)
        with patch("double_ctrl_translator.selection._property_int", side_effect=[2, 0]):
            _verify_uia_window(ctypes.c_void_p(1), self.target)

    def test_clipboard_accepts_cross_process_child_only_in_original_window(self):
        user32 = Mock()
        child = WindowIdentity(20, 99, 4)
        with patch("double_ctrl_translator.selection.ctypes.windll.user32", user32), \
             patch("double_ctrl_translator.selection.window_identity") as identity:
            user32.GetAncestor.return_value = self.target.hwnd
            identity.side_effect = [child, self.target]
            self.assertTrue(_clipboard_window_matches(self.target, child.hwnd))
            identity.side_effect = [child, WindowIdentity(1, 2, 999)]
            self.assertFalse(_clipboard_window_matches(self.target, child.hwnd))
            user32.GetAncestor.return_value = 30
            identity.side_effect = [child]
            self.assertFalse(_clipboard_window_matches(self.target, child.hwnd))

    def test_uia_wins_without_clipboard(self):
        with patch("double_ctrl_translator.selection.verify_foreground"), \
             patch("double_ctrl_translator.selection._uia_probe", return_value=("hello", True)), \
             patch("double_ctrl_translator.selection.clipboard_fallback") as fallback:
            self.assertEqual(get_selected_text(10, self.target), "hello")
            fallback.assert_not_called()

    def test_safe_empty_uia_falls_back_without_global_monkeypatch(self):
        with patch("double_ctrl_translator.selection.verify_foreground"), \
             patch("double_ctrl_translator.selection._uia_probe", return_value=(None, True)), \
             patch("double_ctrl_translator.selection.clipboard_fallback", return_value="world"):
            self.assertEqual(get_selected_text(10, self.target), "world")

    def test_uia_failure_is_fail_closed_for_password_safety(self):
        with patch("double_ctrl_translator.selection.verify_foreground"), \
             patch("double_ctrl_translator.selection._uia_probe",
                   side_effect=SelectionError("UI Automation 读取失败")), \
             patch("double_ctrl_translator.selection.clipboard_fallback") as fallback:
            with self.assertRaises(SelectionError): get_selected_text(10, self.target)
            fallback.assert_not_called()

    def test_cancel_checked_before_selection(self):
        cancel = threading.Event(); cancel.set()
        with patch("double_ctrl_translator.selection._uia_probe") as probe:
            with self.assertRaisesRegex(SelectionError, "取消"):
                get_selected_text(10, self.target, cancel)
            probe.assert_not_called()

    def test_cancel_after_uia_probe_is_checked_before_result_use(self):
        cancel = threading.Event()
        def finish_probe(*_, **__): cancel.set(); return ("late", True)
        with patch("double_ctrl_translator.selection.verify_foreground"), \
             patch("double_ctrl_translator.selection._uia_probe", side_effect=finish_probe):
            with self.assertRaisesRegex(SelectionError, "取消"):
                get_selected_text(10, self.target, cancel)

    def test_only_one_timed_out_uia_probe_can_be_pending(self):
        _uia_pending.acquire()
        try:
            with self.assertRaisesRegex(SelectionError, "仍在进行"):
                from double_ctrl_translator.selection import _uia_probe
                _uia_probe(.01)
        finally:
            _uia_pending.release()

    def test_limit_is_enforced_by_worker_entrypoint(self):
        with patch("double_ctrl_translator.selection.verify_foreground"), \
             patch("double_ctrl_translator.selection._uia_probe", return_value=("12345", True)):
            with self.assertRaises(SelectionError): get_selected_text(4, self.target)

    def test_external_clipboard_update_is_never_overwritten(self):
        snapshot = ClipboardSnapshot([(13, "old")], 10)
        with patch("double_ctrl_translator.selection.win32clipboard.GetClipboardSequenceNumber",
                   return_value=12), \
             patch("double_ctrl_translator.selection.win32clipboard.OpenClipboard") as opened:
            self.assertFalse(snapshot.restore_if_unchanged(11))
            opened.assert_not_called()

    def test_ole_transport_metadata_is_not_read_or_restored(self):
        formats = iter([0xC001, 13, 0xC002, 0xC003, 0])
        names = {0xC001: "DataObject", 0xC002: "Ole Private Data", 0xC003: "Rich Text Format"}
        values = {13: "public text", 0xC003: b"{\\rtf1 public text}"}
        with patch("double_ctrl_translator.selection._open_clipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.CloseClipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.EnumClipboardFormats",
                   side_effect=lambda _: next(formats)), \
             patch("double_ctrl_translator.selection.win32clipboard.GetClipboardFormatName",
                   side_effect=lambda fmt: names[fmt]), \
             patch("double_ctrl_translator.selection.win32clipboard.GetClipboardData",
                   side_effect=lambda fmt: values[fmt]) as read, \
             patch("double_ctrl_translator.selection.win32clipboard.GetClipboardSequenceNumber",
                   return_value=10), \
             patch("double_ctrl_translator.selection.win32clipboard.EmptyClipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.SetClipboardData") as write:
            snapshot = ClipboardSnapshot.capture()
            self.assertTrue(snapshot.restore_if_unchanged(10))
        self.assertEqual([call.args for call in read.call_args_list], [(13,), (0xC003,)])
        self.assertEqual([call.args for call in write.call_args_list], list(values.items()))

    def test_unreadable_user_format_is_named_and_clipboard_is_untouched(self):
        formats = iter([0xC003, 0])
        with patch("double_ctrl_translator.selection._open_clipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.CloseClipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.EnumClipboardFormats",
                   side_effect=lambda _: next(formats)), \
             patch("double_ctrl_translator.selection.win32clipboard.GetClipboardFormatName",
                   return_value="WPS custom document"), \
             patch("double_ctrl_translator.selection.win32clipboard.GetClipboardData",
                   side_effect=RuntimeError("unreadable")), \
             patch("double_ctrl_translator.selection.win32clipboard.EmptyClipboard") as clear:
            with self.assertRaisesRegex(SelectionError, "WPS custom document"):
                ClipboardSnapshot.capture()
        clear.assert_not_called()

    def test_empty_ole1_alias_requires_saved_ole2_representation(self):
        for aliases, alternate in ((["Native", "ObjectLink"], True), (["Native"], False)):
            with self.subTest(alternate=alternate):
                names = {0xC001: "Native", 0xC002: "ObjectLink",
                         0xC003: "Embed Source", 0xC004: "Link Source"}
                formats = iter(([0xC001, 0xC002, 0xC003, 0xC004] if alternate else [0xC001]) + [0])
                def read(fmt):
                    if names[fmt] in aliases:
                        raise pywintypes.error(0, "GetClipboardData", "No error message is available")
                    return b"public OLE2 data"
                with patch("double_ctrl_translator.selection._open_clipboard"), \
                     patch("double_ctrl_translator.selection.win32clipboard.CloseClipboard"), \
                     patch("double_ctrl_translator.selection.win32clipboard.EnumClipboardFormats",
                           side_effect=lambda _: next(formats)), \
                     patch("double_ctrl_translator.selection.win32clipboard.GetClipboardFormatName",
                           side_effect=lambda fmt: names[fmt]), \
                     patch("double_ctrl_translator.selection.win32clipboard.GetClipboardData", side_effect=read), \
                     patch("double_ctrl_translator.selection.win32clipboard.GetClipboardSequenceNumber", return_value=10):
                    if alternate:
                        snapshot = ClipboardSnapshot.capture()
                        self.assertEqual(snapshot.items, [(0xC003, b"public OLE2 data"), (0xC004, b"public OLE2 data")])
                    else:
                        with self.assertRaisesRegex(SelectionError, "替代数据"):
                            ClipboardSnapshot.capture()

    def test_metafile_allocation_failure_does_not_clear_clipboard(self):
        from double_ctrl_translator.clipboard_metafile import ClipboardMetafile
        snapshot = ClipboardSnapshot([(win32con.CF_ENHMETAFILE, ClipboardMetafile(b"bad"))], 10)
        with patch("double_ctrl_translator.selection._open_clipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.CloseClipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.GetClipboardSequenceNumber", return_value=10), \
             patch.object(ClipboardMetafile, "create_handle", side_effect=OSError("allocation failed")), \
             patch("double_ctrl_translator.selection.win32clipboard.EmptyClipboard") as clear:
            with self.assertRaisesRegex(SelectionError, "恢复原剪贴板失败"):
                snapshot.restore_if_unchanged(10)
        clear.assert_not_called()

    def test_failed_metafile_restore_frees_only_untransferred_handles(self):
        from double_ctrl_translator.clipboard_metafile import ClipboardMetafile
        snapshot = ClipboardSnapshot([(3, ClipboardMetafile(b"wmf", (8, 1, 1))),
                                      (14, ClipboardMetafile(b"emf"))], 10)
        with patch("double_ctrl_translator.selection._open_clipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.CloseClipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.GetClipboardSequenceNumber", return_value=10), \
             patch.object(ClipboardMetafile, "create_handle", side_effect=[101, 202]), \
             patch.object(ClipboardMetafile, "free_handle") as free, \
             patch("double_ctrl_translator.selection.win32clipboard.EmptyClipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.SetClipboardData",
                   side_effect=[None, OSError("write failed")]):
            with self.assertRaisesRegex(SelectionError, "恢复原剪贴板失败"):
                snapshot.restore_if_unchanged(10)
        free.assert_called_once_with(202)

    def test_bitmap_handle_is_represented_by_safe_dib_and_terminal_sequence(self):
        formats = iter([win32con.CF_BITMAP, win32con.CF_DIB, 0,
                        win32con.CF_BITMAP, win32con.CF_DIB, 0])
        values = {win32con.CF_DIB: b"public-dib"}
        with patch("double_ctrl_translator.selection._open_clipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.CloseClipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.EnumClipboardFormats",
                   side_effect=lambda _: next(formats)), \
             patch("double_ctrl_translator.selection.win32clipboard.GetClipboardData",
                   side_effect=lambda fmt: values[fmt]) as get_data, \
             patch("double_ctrl_translator.selection.win32clipboard.GetClipboardSequenceNumber",
                   side_effect=[10, 11, 11, 11]):
            snapshot = ClipboardSnapshot.capture()
        self.assertEqual(snapshot.items, [(win32con.CF_DIB, b"public-dib")])
        self.assertEqual(snapshot.sequence, 11)
        self.assertEqual(get_data.call_count, 2)

    def test_bitmap_without_safe_dib_representation_fails_closed(self):
        formats = iter([win32con.CF_BITMAP, 0])
        with patch("double_ctrl_translator.selection._open_clipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.CloseClipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.EnumClipboardFormats",
                   side_effect=lambda _: next(formats)), \
             patch("double_ctrl_translator.selection.win32clipboard.GetClipboardData") as get_data:
            with self.assertRaisesRegex(SelectionError, "GDI"):
                ClipboardSnapshot.capture()
        get_data.assert_not_called()

    def test_unknown_opaque_handle_still_fails_closed(self):
        custom = 0xC123
        formats = iter([custom, 0])
        with patch("double_ctrl_translator.selection._open_clipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.CloseClipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.EnumClipboardFormats",
                   side_effect=lambda _: next(formats)), \
             patch("double_ctrl_translator.selection.win32clipboard.GetClipboardData",
                   return_value=12345):
            with self.assertRaisesRegex(SelectionError, "GDI"):
                ClipboardSnapshot.capture()

    def test_unattributed_sequence_change_is_not_read_or_restored(self):
        snapshot = Mock(sequence=10)
        user32 = Mock()
        user32.GetAsyncKeyState.return_value = 0
        with patch("double_ctrl_translator.selection.ctypes.windll.user32", user32), \
             patch("double_ctrl_translator.selection.verify_foreground"), \
             patch("double_ctrl_translator.selection.ClipboardSnapshot.capture", return_value=snapshot), \
             patch("double_ctrl_translator.selection._send_ctrl_c"), \
             patch("double_ctrl_translator.selection._clipboard_owner_matches", return_value=False), \
             patch("double_ctrl_translator.selection.win32clipboard.GetClipboardSequenceNumber", return_value=11):
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
        with patch("double_ctrl_translator.selection.ctypes.windll.user32", user32), \
             patch("double_ctrl_translator.selection.window_identity",
                   return_value=WindowIdentity(20, 2, 3)):
            self.assertTrue(_clipboard_owner_matches(self.target))

    def test_hidden_broker_must_match_embedded_focused_process_lifetime(self):
        user32 = Mock()
        user32.GetAncestor.return_value = 20
        user32.IsWindowVisible.return_value = False
        with patch("double_ctrl_translator.selection.ctypes.windll.user32", user32), \
             patch("double_ctrl_translator.selection.window_identity", return_value=WindowIdentity(20, 99, 4)), \
             patch("double_ctrl_translator.selection._native_focused_window") as focused:
            focused.return_value = WindowIdentity(30, 99, 4)
            self.assertTrue(_clipboard_window_matches(self.target, 20))
            focused.return_value = WindowIdentity(30, 99, 999)
            self.assertFalse(_clipboard_window_matches(self.target, 20))
            focused.return_value = WindowIdentity(30, 100, 4)
            self.assertFalse(_clipboard_window_matches(self.target, 20))
            focused.side_effect = SelectionError("focus changed")
            self.assertFalse(_clipboard_window_matches(self.target, 20))

    def test_native_focus_must_belong_to_original_root_window(self):
        user32 = Mock()
        def get_info(thread, info):
            info._obj.hwndFocus = 30
            return True
        user32.GetGUIThreadInfo.side_effect = get_info
        user32.GetAncestor.return_value = self.target.hwnd
        child = WindowIdentity(30, 99, 4)
        with patch("double_ctrl_translator.selection.ctypes.windll.user32", user32), \
             patch("double_ctrl_translator.selection.window_identity", side_effect=[self.target, child]) as identity:
            self.assertEqual(_native_focused_window(self.target), child)
            user32.GetAncestor.return_value = 999
            with self.assertRaisesRegex(SelectionError, "身份已变化"):
                _native_focused_window(self.target)
            identity.assert_called_with(30)

    def test_unknown_other_process_reused_pid_and_visible_other_document_are_rejected(self):
        user32 = Mock()
        user32.IsWindow.return_value = True
        user32.GetAncestor.side_effect = lambda hwnd, _: hwnd
        user32.IsWindowVisible.return_value = False
        with patch("double_ctrl_translator.selection.ctypes.windll.user32", user32), \
             patch("double_ctrl_translator.selection.window_identity") as identity:
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
        with patch("double_ctrl_translator.selection.ctypes.windll.user32", user32), \
             patch("double_ctrl_translator.selection.verify_foreground"), \
             patch("double_ctrl_translator.selection.ClipboardSnapshot.capture", return_value=snapshot), \
             patch("double_ctrl_translator.selection._send_ctrl_c"), \
             patch("double_ctrl_translator.selection._open_clipboard"), \
             patch("double_ctrl_translator.selection.win32clipboard.CloseClipboard"), \
             patch("double_ctrl_translator.selection._clipboard_owner_matches",
                   side_effect=[True, False]), \
             patch("double_ctrl_translator.selection.win32clipboard.GetClipboardSequenceNumber",
                   side_effect=[11, 11]), \
             patch("double_ctrl_translator.selection.win32clipboard.GetClipboardData") as read:
            with self.assertRaisesRegex(SelectionError, "来源已变化"):
                clipboard_fallback(self.target)
            read.assert_not_called()
        snapshot.restore_if_unchanged.assert_called_once_with(None)


if __name__ == "__main__": unittest.main()
