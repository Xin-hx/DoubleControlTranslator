import os
import sys
import unittest
from unittest.mock import patch

from double_ctrl_translator.lifecycle import LifecycleError, detect_local_desktop


class LifecycleTests(unittest.TestCase):
    def test_remote_or_unbound_backend_is_rejected(self):
        with patch.object(sys, "platform", "win32"), \
             patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(LifecycleError): detect_local_desktop()

    def test_non_windows_backend_is_rejected(self):
        with patch.object(sys, "platform", "linux"):
            with self.assertRaisesRegex(LifecycleError, "远程"):
                detect_local_desktop()


if __name__ == "__main__": unittest.main()
