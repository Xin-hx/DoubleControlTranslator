"""Match Windows application privileges only after an explicit restart action."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys

import psutil
import win32api
import win32security


def process_elevated(pid=None):
    process = token = None
    try:
        process = win32api.OpenProcess(0x1000, False, pid or os.getpid())
        token = win32security.OpenProcessToken(process, win32security.TOKEN_QUERY)
        return bool(win32security.GetTokenInformation(token, win32security.TokenElevation))
    except (OSError, win32api.error):
        return None
    finally:
        if token:
            token.Close()
        if process:
            process.Close()


def privilege_status():
    from ctypes import wintypes
    elevated = process_elevated()
    pid = wintypes.DWORD()
    user = ctypes.windll.user32
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(pid))
    target = process_elevated(pid.value) if pid.value else None
    return {'elevated': elevated, 'elevation_mismatch': elevated is False and target is True}


def restart_command():
    args = ['--restart-after', str(os.getpid()), str(psutil.Process().create_time())]
    if getattr(sys, 'frozen', False):
        return [sys.executable, *args]
    executable = Path(sys.executable)
    windowed = executable.with_name('pythonw.exe')
    return [str(windowed if windowed.is_file() else executable), '-m', 'double_ctrl_translator', *args]


def launch_elevated():
    from win32com.shell import shell, shellcon
    command = restart_command()
    # Windows owns the consent prompt. Cancellation raises before the old app exits.
    result = shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_NOCLOSEPROCESS,
        lpVerb='runas', lpFile=command[0], lpParameters=subprocess.list2cmdline(command[1:]),
        nShow=1)
    result['hProcess'].Close()


def wait_for_previous_instance():
    if '--restart-after' not in sys.argv:
        return
    index = sys.argv.index('--restart-after')
    pid, created = int(sys.argv[index + 1]), float(sys.argv[index + 2])
    try:
        process = psutil.Process(pid)
        if abs(process.create_time() - created) < .01:
            process.wait(30)
    except psutil.NoSuchProcess:
        pass
    del sys.argv[index:index + 3]
