"""Opt-in real Hermes restart acceptance; uses normal taskkill (never /F or /T).

Stops/restarts the user's Hermes desktop to verify the installed startup bridge.
Does not click or send keystrokes into Hermes windows. Restores Hermes at the end.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from double_ctrl_translator.settings import Settings

APP = Path(os.environ['LOCALAPPDATA']) / 'Programs/DoubleCtrlTranslator/DoubleCtrlTranslator.exe'


def processes(name):
    return [p for p in psutil.process_iter(['name', 'cmdline', 'exe']) if p.info['name'] == name]


def hermes_main():
    return next((p for p in processes('Hermes.exe')
                 if not any(arg.startswith('--type=') for arg in p.info['cmdline'] or [])), None)


def app_main():
    return next((p for p in processes('DoubleCtrlTranslator.exe') if p.info['exe'] and Path(p.info['exe']) == APP
                 and not any(arg in {'--worker', '--model-guard'} for arg in p.info['cmdline'] or [])), None)


def wait_for(action, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = action()
        if value:
            return value
        time.sleep(.25)
    raise TimeoutError('Hermes lifecycle acceptance timed out')


def close_hermes(process):
    command = subprocess.run(['taskkill', '/PID', str(process.pid)], capture_output=True)
    if command.returncode:
        raise RuntimeError('Normal Hermes shutdown was refused; no force termination attempted')
    wait_for(lambda: not psutil.pid_exists(process.pid), 25)


def main():
    original = hermes_main()
    assert original, 'Start Hermes before running the real host lifecycle acceptance'
    executable = Path(original.info['exe'])
    assert Settings.load().launch_with_hermes
    assert not Settings.load().listening, 'Pause listening before this resource-isolated acceptance'
    report = {}
    try:
        previous_app = app_main()
        if previous_app:
            subprocess.run([str(APP), '--quit'], check=True, timeout=15)
            wait_for(lambda: not psutil.pid_exists(previous_app.pid), 15)
        close_hermes(original)
        print('original Hermes closed normally', flush=True)
        env = {k: v for k, v in os.environ.items() if k.upper() not in {'__COMPAT_LAYER', 'ELECTRON_RUN_AS_NODE'}}
        subprocess.Popen([str(executable)], env=env)
        tool = wait_for(app_main, 90)
        assert tool.parent().name().lower() == 'explorer.exe'
        report['real_hermes_start_opened_app'] = True
        print('real Hermes startup bridge opened independent app', flush=True)
        new_host = wait_for(hermes_main)
        close_hermes(new_host)
        time.sleep(2)
        assert tool.is_running()
        report['app_survived_real_hermes_exit'] = True
        print('app survived real Hermes exit', flush=True)
        assert hermes_main() is None
        subprocess.run([sys.executable, str(ROOT / 'tests/integration_standalone.py')], check=True, timeout=150)
        report['real_selection_translation_with_hermes_closed'] = True
    finally:
        if hermes_main() is None:
            env = {k: v for k, v in os.environ.items() if k.upper() not in {'__COMPAT_LAYER', 'ELECTRON_RUN_AS_NODE'}}
            subprocess.Popen([str(executable)], env=env)
            wait_for(hermes_main, 30)
    (ROOT / 'runtime/host-lifecycle-acceptance.json').write_text(json.dumps(report, indent=2), 'utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
