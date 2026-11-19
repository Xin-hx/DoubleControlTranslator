from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys


def child_command(mode: str, *args: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--" + mode, *args]
    return [sys.executable, "-m", "double_ctrl_translator", "--" + mode, *args]


def child_environment() -> dict:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def external_popen(command, **kwargs):
    # PyInstaller changes Windows DLL lookup; external CUDA binaries need their own DLLs.
    if getattr(sys, "frozen", False):
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        env = os.environ.copy()
        bundle = str(getattr(sys, "_MEIPASS", ""))
        if bundle:
            env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                          if not p.lower().startswith(bundle.lower()))
        for key in list(env):
            if key.startswith("QT_") or key.startswith("HERMES_PARENT_"):
                env.pop(key, None)
        return subprocess.Popen(command, env=env, **kwargs)
    finally:
        if getattr(sys, "frozen", False):
            ctypes.windll.kernel32.SetDllDirectoryW(sys._MEIPASS)


def pipe_stdio() -> None:
    # The windowed PyInstaller bootloader sets Python stdio to None, even for inherited pipes.
    import msvcrt
    kernel = ctypes.windll.kernel32
    kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
    kernel.GetStdHandle.restype = ctypes.c_void_p
    for name, number, mode in (("stdin", -10, "r"), ("stdout", -11, "w")):
        if getattr(sys, name) is None:
            handle = kernel.GetStdHandle(number & 0xffffffff)
            if not handle or handle == ctypes.c_void_p(-1).value:
                raise RuntimeError("内部进程缺少控制管道")
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY if mode == "r" else os.O_WRONLY)
            setattr(sys, name, os.fdopen(fd, mode, encoding="utf-8", buffering=1))
        # Frozen interpreters may ignore PYTHONIOENCODING; configure existing streams too.
        getattr(sys, name).reconfigure(encoding="utf-8", errors="strict")
