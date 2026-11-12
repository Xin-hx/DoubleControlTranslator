"""Packaged single-instance and real Explorer-parent startup acceptance."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import psutil

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / 'dist/HermesTool/HermesTool.exe'
spec = importlib.util.spec_from_file_location('test_bridge_launcher', ROOT / 'plugin/dashboard/launcher.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def main():
    pid = launcher.launch_independent(EXE)
    process = psutil.Process(pid)
    try:
        assert process.parent().name().lower() == 'explorer.exe'
        time.sleep(2)
        assert process.is_running()
        repeated = subprocess.run([str(EXE)], timeout=10)
        assert repeated.returncode == 0
        assert process.is_running()
        instances = [p for p in psutil.process_iter(['exe', 'cmdline'])
                     if p.info['exe'] and Path(p.info['exe']) == EXE
                     and '--worker' not in (p.info['cmdline'] or []) and '--model-guard' not in (p.info['cmdline'] or [])]
        assert len(instances) == 1, [p.pid for p in instances]
        stopped = subprocess.run([str(EXE), '--quit'], timeout=15)
        assert stopped.returncode == 0
        process.wait(15)
        result = {'explorer_parent': True, 'repeat_launch_reused_instance': True, 'graceful_quit': True,
                  'real_hermes_restart_tested': False}
        (ROOT / 'runtime/bridge-acceptance.json').write_text(json.dumps(result, indent=2), 'utf-8')
        print(json.dumps(result))
    finally:
        if psutil.pid_exists(pid):
            process.terminate()


if __name__ == '__main__':
    main()
