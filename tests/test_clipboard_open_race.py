"""An untrusted check/open race must not become a restore token."""
import unittest
from unittest.mock import Mock, patch
from hermes_selection_translate.selection import SelectionError, WindowIdentity, clipboard_fallback


class ClipboardOpenRaceTests(unittest.TestCase):
    def test_external_owner_before_open_is_never_restored_over(self):
        snapshot = Mock(sequence=10)
        user32 = Mock()
        user32.GetAsyncKeyState.return_value = 0
        with patch('hermes_selection_translate.selection.ctypes.windll.user32', user32), \
             patch('hermes_selection_translate.selection.verify_foreground'), \
             patch('hermes_selection_translate.selection.ClipboardSnapshot.capture', return_value=snapshot), \
             patch('hermes_selection_translate.selection._send_ctrl_c'), \
             patch('hermes_selection_translate.selection._open_clipboard'), \
             patch('hermes_selection_translate.selection._clipboard_owner_matches', side_effect=[True, False]), \
             patch('hermes_selection_translate.selection.win32clipboard.GetClipboardSequenceNumber', side_effect=[11, 12]), \
             patch('hermes_selection_translate.selection.win32clipboard.CloseClipboard'):
            with self.assertRaisesRegex(SelectionError, '来源已变化'):
                clipboard_fallback(WindowIdentity(1, 2, 3))
        snapshot.restore_if_unchanged.assert_called_once_with(None)


if __name__ == '__main__':
    unittest.main()
