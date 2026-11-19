"""An untrusted check/open race must not become a restore token."""
import unittest
from unittest.mock import Mock, patch
from double_ctrl_translator.selection import SelectionError, WindowIdentity, clipboard_fallback


class ClipboardOpenRaceTests(unittest.TestCase):
    def test_external_owner_before_open_is_never_restored_over(self):
        snapshot = Mock(sequence=10)
        user32 = Mock()
        user32.GetAsyncKeyState.return_value = 0
        with patch('double_ctrl_translator.selection.ctypes.windll.user32', user32), \
             patch('double_ctrl_translator.selection.verify_foreground'), \
             patch('double_ctrl_translator.selection.ClipboardSnapshot.capture', return_value=snapshot), \
             patch('double_ctrl_translator.selection._send_ctrl_c'), \
             patch('double_ctrl_translator.selection._open_clipboard'), \
             patch('double_ctrl_translator.selection._clipboard_owner_matches', side_effect=[True, False]), \
             patch('double_ctrl_translator.selection.win32clipboard.GetClipboardSequenceNumber', side_effect=[11, 12]), \
             patch('double_ctrl_translator.selection.win32clipboard.CloseClipboard'):
            with self.assertRaisesRegex(SelectionError, '来源已变化'):
                clipboard_fallback(WindowIdentity(1, 2, 3))
        snapshot.restore_if_unchanged.assert_called_once_with(None)


if __name__ == '__main__':
    unittest.main()
