"""Small Qt shell acceptance, using only this program's own widget methods."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from hermes_selection_translate.app import MainWindow
from hermes_selection_translate.qt_ui import apply_style


class QtShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        apply_style(cls.app)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'HERMESTOOL_HOME': self.temp.name, 'HERMES_HOME': str(Path(self.temp.name) / 'absent')})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def settle(self, window):
        deadline = time.monotonic() + 4
        while window.polling and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.app.processEvents()

    def dispose(self, window):
        window.timer.stop()
        window.tray.hide()
        window.quitting = True
        window.close()
        window.deleteLater()
        self.app.processEvents()

    def test_close_to_tray_restore_and_render(self):
        with patch('hermes_selection_translate.app.QSystemTrayIcon.isSystemTrayAvailable', return_value=True):
            window = MainWindow(self.app)
            try:
                window.show()
                self.settle(window)
                self.assertTrue(window.tray.isVisible())
                window.close()
                self.assertFalse(window.isVisible())
                window.restore()
                self.app.processEvents()
                self.assertTrue(window.isVisible())
                self.assertEqual(window.size().width(), 560)
                window.show_details(True)
                self.assertTrue(window.details.isVisible())
            finally:
                self.dispose(window)

    def test_no_tray_keeps_window_visible(self):
        with patch('hermes_selection_translate.app.QSystemTrayIcon.isSystemTrayAvailable', return_value=False):
            window = MainWindow(self.app)
            try:
                window.show()
                self.settle(window)
                window.close()
                self.app.processEvents()
                self.assertTrue(window.isVisible())
                self.assertFalse(window.quitting)
                self.assertIn('系统托盘不可用', window.message.text())
            finally:
                self.dispose(window)

    def test_declining_admin_restart_preserves_running_application(self):
        window = MainWindow(self.app)
        try:
            self.settle(window)
            cancelled = Exception('cancelled')
            cancelled.winerror = 1223
            with patch('hermes_selection_translate.app.launch_elevated', side_effect=cancelled), patch.object(window, 'quit') as quit_app:
                window.restart_elevated()
                quit_app.assert_not_called()
                self.assertFalse(window.quitting)
            with patch('hermes_selection_translate.app.launch_elevated'), patch.object(window, 'quit') as quit_app:
                window.restart_elevated()
                quit_app.assert_called_once()
        finally:
            self.dispose(window)


if __name__ == '__main__':
    unittest.main()
