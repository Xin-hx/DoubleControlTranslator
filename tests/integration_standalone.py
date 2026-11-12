"""Necessary native acceptance: real packaged worker + GGUF + UIA + Qt card.

Uses only an isolated test window and explicit test-mode pipes. Does not send
keystrokes into user applications or change shared model state.
"""
from __future__ import annotations

import ctypes
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

import psutil
import win32clipboard

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hermes_selection_translate.manager import WorkerManager

EXE = ROOT / 'dist/HermesTool/HermesTool.exe'


def command(mode, *args):
    extra = ['--allow-test-marker'] if mode == 'worker' else []
    return [str(EXE), '--' + mode, *args, *extra]


def wait_for(test, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if test():
            return
        time.sleep(.1)
    raise AssertionError('acceptance condition timed out')


def main():
    manager = WorkerManager()
    host = None
    pids = []
    result = {}
    try:
        with patch('hermes_selection_translate.manager.child_command', side_effect=command), patch('hermes_selection_translate.local_model.child_command', side_effect=command), patch('hermes_selection_translate.manager.resolve_healthy_endpoint', side_effect=RuntimeError('force independent path')):
            started = time.monotonic()
            state = manager.enable_and_load()
            assert state['state'] == 'running', state
            assert state['model'] == 'project', state
            pids = [manager.process.pid, manager.model.process.pid, manager.model.server_pid]
            result['independent_model_ready_s'] = round(time.monotonic() - started, 2)
            user = ctypes.windll.user32
            from ctypes import wintypes
            foreground_pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(foreground_pid))
            locked = psutil.Process(foreground_pid.value).name().lower() in {'lockapp.exe', 'logonui.exe'}
            sequence = win32clipboard.GetClipboardSequenceNumber()
            foreground = user.GetForegroundWindow()
            if locked:
                result['real_selection_blocked'] = 'Windows desktop is locked'
                manager._send({'op': 'test', 'text': 'The quick brown fox jumps over the lazy dog.'})
            else:
                host = subprocess.Popen([sys.executable, str(ROOT / 'tests/win32_edit_host.py')],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                info = json.loads(host.stdout.readline())
                current = ctypes.windll.kernel32.GetCurrentThreadId()
                previous = user.GetWindowThreadProcessId(user.GetForegroundWindow(), None)
                target = user.GetWindowThreadProcessId(info['hwnd'], None)
                if previous:
                    user.AttachThreadInput(current, previous, True)
                user.AttachThreadInput(current, target, True)
                try:
                    user.BringWindowToTop(info['hwnd'])
                    user.SetForegroundWindow(info['hwnd'])
                    user.SetFocus(info['edit'])
                finally:
                    user.AttachThreadInput(current, target, False)
                    if previous:
                        user.AttachThreadInput(current, previous, False)
                time.sleep(.4)
                foreground = user.GetForegroundWindow()
                assert foreground == info['hwnd'], 'test fixture did not receive foreground'
                manager._send({'op': 'test-selection', 'hwnd': info['hwnd']})
            def translated():
                manager._send({'op': 'status'})
                return bool(manager.diagnostics.get('test_result'))
            wait_for(translated, 90)
            text = manager.diagnostics['test_result']
            assert any('\u4e00' <= c <= '\u9fff' for c in text), text
            assert manager.diagnostics['test_card_visible']
            if not locked:
                assert ctypes.windll.user32.GetForegroundWindow() == foreground, 'card stole focus'
            assert win32clipboard.GetClipboardSequenceNumber() == sequence, 'UIA changed clipboard'
            result.update(real_model_translation=True, real_selection_translated=not locked, qt_card_visible=True, focus_preserved=None if locked else True,
                          clipboard_preserved=True, first_token_ms=manager.diagnostics.get('last_first_token_ms'))
            manager._send({'op': 'cancel'})
            def closed():
                manager._send({'op': 'status'})
                return manager.diagnostics.get('test_card_visible') is False
            wait_for(closed)
            result['card_cancelled'] = True
            manager.disable()
            wait_for(lambda: all(not psutil.pid_exists(pid) for pid in pids))
            result['pause_released_all_owned_processes'] = True
    finally:
        manager.disable()
        if host and host.poll() is None:
            host.terminate()
            host.wait(5)
    (ROOT / 'runtime/standalone-acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
