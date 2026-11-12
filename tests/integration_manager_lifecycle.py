"""Production manager/worker/owned-model guard lifecycle under the real Hermes ancestor."""
from __future__ import annotations

import json
from pathlib import Path
import psutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hermes_selection_translate.manager import WorkerManager

manager = WorkerManager(ROOT)
token = manager.attach_session()
owned_pids = []
try:
    enabled = manager.enable_and_load()
    assert enabled["enabled"] and enabled["state"] == "running", enabled
    worker_pid = manager.process.pid
    model_state = enabled
    assert model_state["model"] == "project", model_state
    guard_pid = manager.model.process.pid
    owned_pids = [guard_pid] + [child.pid for child in psutil.Process(guard_pid).children(recursive=True)]
    trial = manager.test_card()
    assert trial["accepted"], trial
    time.sleep(2)
    disabled = manager.disable()
    assert not disabled["enabled"] and not manager.model.running(), disabled
    time.sleep(.5)
    assert not psutil.pid_exists(worker_pid)
    assert all(not psutil.pid_exists(pid) for pid in owned_pids)

    # Re-enable and prove the Job Object closes the llama child even when its
    # guard is forcibly killed rather than allowed to run normal cleanup.
    enabled_again = manager.enable()
    assert enabled_again["enabled"]
    second_worker = manager.process.pid
    assert manager.start_model()["model"] == "project"
    guard = manager.model.process
    child_pids = [child.pid for child in psutil.Process(guard.pid).children(recursive=True)]
    assert child_pids
    guard.kill(); guard.wait(4)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(psutil.pid_exists(pid) for pid in child_pids):
        time.sleep(.1)
    assert all(not psutil.pid_exists(pid) for pid in child_pids)

    # Simulate the persistent SDK lifecycle socket closing. Last-session
    # detach must stop the worker even though the backend itself stays alive.
    manager.detach_session(token); token = None
    time.sleep(.5)
    assert not psutil.pid_exists(second_worker)
    print(json.dumps({"ok": True, "worker_started": True,
                      "owned_model_guard_started": True,
                      "single_enable_and_load_ready": True,
                      "test_card_accepted": True,
                      "disable_cleaned_worker_guard_and_llama": True,
                      "guard_hard_kill_job_cleaned_llama": True,
                      "last_lifecycle_session_disconnect_stopped_worker": True}))
finally:
    if token: manager.detach_session(token)
    manager.disable()
