import io
import json
from pathlib import Path
import threading
import time
import subprocess
import sys
import psutil
import unittest
from unittest.mock import patch

from hermes_selection_translate.lifecycle import DesktopIdentity
from hermes_selection_translate.local_model import LocalModelError, OwnedModel
from hermes_selection_translate.manager import WorkerManager
from hermes_selection_translate.process_tree import owned_tree, stop_owned


class _Pipe:
    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.pid = 101
    def poll(self): return None


class LoadingCancellationTests(unittest.TestCase):
    def test_status_and_disable_do_not_wait_for_blocked_model_ready(self):
        manager = WorkerManager(Path.cwd())
        manager.process = _Pipe()
        manager.process_created = 1.0
        manager.desktop = DesktopIdentity(1, 1.0)
        manager.transition = "starting_model"
        entered = threading.Event()
        release = threading.Event()

        def blocked_start(_desktop):
            entered.set(); release.wait(5); raise LocalModelError("cancelled")

        with patch.object(manager, "_worker_running", side_effect=lambda: manager.process is not None), \
             patch.object(manager.model, "start", side_effect=blocked_start), \
             patch.object(manager.model, "stop") as stop, \
             patch.object(manager, "_stop_worker_locked", side_effect=lambda: setattr(manager, "process", None)):
            thread = threading.Thread(target=manager.start_model)
            manager.transition = None
            thread.start(); self.assertTrue(entered.wait(1))
            before = time.monotonic(); state = manager.status()
            self.assertLess(time.monotonic() - before, .25)
            self.assertEqual(state["state"], "starting_model")
            before = time.monotonic(); manager.disable()
            self.assertLess(time.monotonic() - before, .5)
            stop.assert_called()
            release.set(); thread.join(2)
            self.assertIsNone(manager.process)


class OwnedTreeTests(unittest.TestCase):
    def test_launcher_and_long_lived_child_are_cleaned_by_identity_only(self):
        launcher = subprocess.Popen(
            [sys.executable, "-c", "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)"],
            stdout=subprocess.PIPE, text=True)
        child_pid = int(launcher.stdout.readline())
        created = psutil.Process(launcher.pid).create_time()
        identities = owned_tree(launcher.pid, created)
        self.assertIn(child_pid, {pid for pid, _ in identities})
        self.assertNotIn(psutil.Process().pid, {pid for pid, _ in identities})
        stop_owned(identities, 1.0)
        launcher.wait(3)
        launcher.stdout.close()
        self.assertFalse(psutil.pid_exists(child_pid))


if __name__ == "__main__":
    unittest.main()
