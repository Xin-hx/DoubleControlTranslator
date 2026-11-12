"""Hermes-only bridge. No translator runtime or UI is imported here."""
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import subprocess
import logging


def _trusted_desktop():
    import psutil
    pid = int(os.environ.get("HERMES_PARENT_PID", "0"))
    marker = os.environ.get("HERMES_PARENT_START_MARKER", "")
    parent = next((p for p in psutil.Process().parents() if p.pid == pid), None)
    return bool(parent and parent.name().lower() == "hermes.exe" and marker.startswith("winms:")
                and abs(parent.create_time() - int(marker[6:]) / 1000) < .01)


class STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", W.DWORD), ("lpReserved", W.LPWSTR), ("lpDesktop", W.LPWSTR),
                ("lpTitle", W.LPWSTR), ("dwX", W.DWORD), ("dwY", W.DWORD),
                ("dwXSize", W.DWORD), ("dwYSize", W.DWORD), ("dwXCountChars", W.DWORD),
                ("dwYCountChars", W.DWORD), ("dwFillAttribute", W.DWORD), ("dwFlags", W.DWORD),
                ("wShowWindow", W.WORD), ("cbReserved2", W.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", W.HANDLE), ("hStdOutput", W.HANDLE), ("hStdError", W.HANDLE)]


class STARTUPINFOEX(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFO), ("lpAttributeList", ctypes.c_void_p)]


class PROCESSINFO(ctypes.Structure):
    _fields_ = [("hProcess", W.HANDLE), ("hThread", W.HANDLE), ("dwProcessId", W.DWORD), ("dwThreadId", W.DWORD)]


def launch_independent(executable: Path):
    """Use Explorer as the real Windows parent so Hermes taskkill /T cannot reach us."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetShellWindow.restype = W.HWND
    user.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    pid = W.DWORD()
    user.GetWindowThreadProcessId(user.GetShellWindow(), ctypes.byref(pid))
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    parent = kernel.OpenProcess(0x0080, False, pid.value)  # PROCESS_CREATE_PROCESS
    if not parent:
        raise ctypes.WinError(ctypes.get_last_error())
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, W.DWORD, W.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, W.DWORD, ctypes.c_size_t,
                                                ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    kernel.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    size = ctypes.c_size_t()
    kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    attributes = ctypes.create_string_buffer(size.value)
    initialized = False
    try:
        if not kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        initialized = True
        handle = W.HANDLE(parent)
        if not kernel.UpdateProcThreadAttribute(attributes, 0, 0x00020000, ctypes.byref(handle), ctypes.sizeof(handle), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        startup = STARTUPINFOEX()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
        info = PROCESSINFO()
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(executable), "--from-hermes"]))
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("HERMES_PARENT_") and k.upper() != "__COMPAT_LAYER"}
        block = ctypes.create_unicode_buffer("\0".join(f"{k}={v}" for k, v in sorted(env.items(), key=lambda i: i[0].upper())) + "\0\0")
        kernel.CreateProcessW.argtypes = [W.LPCWSTR, W.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
            W.BOOL, W.DWORD, ctypes.c_void_p, W.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(PROCESSINFO)]
        if not kernel.CreateProcessW(str(executable), command, None, None, False,
                                    0x00080000 | 0x00000400 | 0x08000000, block,
                                    str(executable.parent), ctypes.byref(startup), ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(info.hThread)
        kernel.CloseHandle(info.hProcess)
        return info.dwProcessId
    finally:
        if initialized:
            kernel.DeleteProcThreadAttributeList(attributes)
        kernel.CloseHandle(parent)


def launch_from_hermes():
    try:
        if os.name != "nt" or not _trusted_desktop():
            return
        directory = Path(os.environ["LOCALAPPDATA"]) / "HermesTool"
        settings_path = directory / "settings.json"
        if settings_path.exists() and not json.loads(settings_path.read_text("utf-8")).get("launch_with_hermes", True):
            return
        manifest = json.loads((directory / "bridge.json").read_text("utf-8-sig"))
        executable = Path(manifest["executable"])
        if executable.is_file():
            launch_independent(executable)
    except Exception:
        logging.getLogger(__name__).exception("HermesTool startup bridge failed")
