from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.request

import psutil


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
                ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def create_kill_on_close_job():
    kernel32 = ctypes.windll.kernel32
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                  ctypes.c_void_p, wintypes.DWORD)
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        kernel32.CloseHandle(job)
        return None
    return job


def desktop_alive(pid: int, created: float) -> bool:
    try:
        process = psutil.Process(pid)
        return abs(process.create_time() - created) < 0.01
    except (psutil.Error, OSError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desktop-pid", type=int, required=True)
    parser.add_argument("--desktop-created", type=float, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--load-timeout", type=float, default=180.0)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    runtime = args.runtime
    model = args.model
    if not desktop_alive(args.desktop_pid, args.desktop_created):
        print(json.dumps({"ready": False, "error": "独立程序进程身份无效"}), flush=True)
        return 2
    if not runtime.is_file() or not model.is_file():
        print(json.dumps({"ready": False, "error": "找不到 llama-server 或 Qwen 模型文件"}), flush=True)
        return 2
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()

    def input_reader() -> None:
        for line in sys.stdin:
            try:
                if json.loads(line).get("op") == "stop":
                    stop.set()
                    return
            except Exception:
                continue
        stop.set()  # backend died or closed the ownership pipe

    threading.Thread(target=input_reader, name="model-owner-pipe", daemon=True).start()
    command = [str(runtime), "--model", str(model), "--host", "127.0.0.1",
               "--port", str(args.port), "--ctx-size", "4096", "--parallel", "1",
               "--jinja", "--reasoning", "off", "--no-webui"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    job = create_kill_on_close_job()
    if not job:
        print(json.dumps({"ready": False, "error": "无法建立项目模型进程作业"}), flush=True)
        return 3
    with (log_dir / "owned-llama.log").open("a", encoding="utf-8") as log:
        from .launch import external_popen
        child = external_popen(command, cwd=runtime.parent, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=flags, shell=False)
        kernel32 = ctypes.windll.kernel32
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(child._handle)):
            child.terminate(); child.wait(4); kernel32.CloseHandle(job)
            print(json.dumps({"ready": False, "error": "无法绑定项目模型进程作业"}), flush=True)
            return 3
        child_created = psutil.Process(child.pid).create_time()
        try:
            deadline = time.monotonic() + args.load_timeout
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            ready = False
            while time.monotonic() < deadline and not stop.is_set():
                if child.poll() is not None or not desktop_alive(args.desktop_pid, args.desktop_created):
                    break
                try:
                    with opener.open(f"http://127.0.0.1:{args.port}/health", timeout=1.0) as response:
                        if response.status == 200:
                            ready = True
                            break
                except OSError:
                    pass
                time.sleep(0.25)
            if not ready:
                print(json.dumps({"ready": False, "error": "项目翻译模型启动失败或加载超时"}), flush=True)
                return 3
            print(json.dumps({"ready": True, "pid": child.pid}), flush=True)
            while not stop.wait(0.5):
                if child.poll() is not None or not desktop_alive(args.desktop_pid, args.desktop_created):
                    break
        finally:
            try:
                live = (child.poll() is None and
                        abs(psutil.Process(child.pid).create_time() - child_created) < 0.01)
            except (psutil.Error, OSError):
                live = False
            if live:
                try:
                    child.terminate()
                    child.wait(8)
                except Exception:
                    if child.poll() is None:
                        child.kill()
                        child.wait(4)
            kernel32.CloseHandle(job)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
