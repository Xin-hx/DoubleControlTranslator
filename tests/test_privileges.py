import ctypes
import sys
import unittest
from ctypes import wintypes
from unittest.mock import Mock, patch

from double_ctrl_translator.privileges import privilege_status, wait_for_previous_instance, launch_elevated


class PrivilegeTests(unittest.TestCase):
    def test_admin_foreground_is_reported_only_for_non_admin_listener(self):
        user = Mock()
        def set_pid(hwnd, ptr):
            ctypes.cast(ptr, ctypes.POINTER(wintypes.DWORD)).contents.value = 123
        user.GetWindowThreadProcessId.side_effect = set_pid
        for own, target, expected in ((False, True, True), (True, True, False), (False, False, False), (False, None, False)):
            with patch('double_ctrl_translator.privileges.ctypes.windll.user32', user), patch('double_ctrl_translator.privileges.process_elevated', side_effect=[own, target]):
                self.assertEqual(privilege_status()['elevation_mismatch'], expected)

    def test_restart_does_not_wait_on_recycled_pid(self):
        process = Mock()
        process.create_time.return_value = 999.0
        with patch.object(sys, 'argv', ['app', '--restart-after', '123', '456.0']), patch('psutil.Process', return_value=process):
            wait_for_previous_instance()
            self.assertEqual(sys.argv, ['app'])
        process.wait.assert_not_called()

    def test_elevation_is_explicit_runas_and_releases_process_handle(self):
        from win32com.shell import shell
        handle = Mock()
        with patch('double_ctrl_translator.privileges.restart_command', return_value=['C:/Program Files/DoubleCtrlTranslator.exe', '--restart-after', '123', '456.0']), patch.object(shell, 'ShellExecuteEx', return_value={'hProcess': handle}) as execute:
            launch_elevated()
        self.assertEqual(execute.call_args.kwargs['lpVerb'], 'runas')
        self.assertEqual(execute.call_args.kwargs['lpFile'], 'C:/Program Files/DoubleCtrlTranslator.exe')
        self.assertIn('--restart-after', execute.call_args.kwargs['lpParameters'])
        handle.Close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
