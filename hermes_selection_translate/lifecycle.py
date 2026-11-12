from __future__ import annotations

from dataclasses import dataclass
import os
import sys

import psutil


class LifecycleError(RuntimeError):
    pass


@dataclass(frozen=True)
class DesktopIdentity:
    pid: int
    created: float

    def alive(self) -> bool:
        try:
            process = psutil.Process(self.pid)
            return (process.name().lower() == "hermes.exe" and
                    abs(process.create_time() - self.created) < 0.01)
        except (psutil.Error, OSError):
            return False


def detect_local_desktop() -> DesktopIdentity:
    """Bind this backend to the Hermes.exe ancestor that actually spawned it."""
    if sys.platform != "win32":
        raise LifecycleError("仅支持本机 Windows Hermes Desktop；远程后端不受支持")
    raw_pid = os.environ.get("HERMES_PARENT_PID", "")
    raw_marker = os.environ.get("HERMES_PARENT_START_MARKER", "")
    try:
        expected_pid = int(raw_pid)
    except ValueError as exc:
        raise LifecycleError("后端没有可信的 Hermes Desktop 父进程身份") from exc
    ancestors = {process.pid: process for process in psutil.Process(os.getpid()).parents()}
    parent = ancestors.get(expected_pid)
    if parent is None:
        raise LifecycleError("后端不是本机 Hermes Desktop 的后代进程")
    try:
        created = parent.create_time()
        if parent.name().lower() != "hermes.exe":
            raise LifecycleError("父进程不是 Hermes Desktop")
    except (psutil.Error, OSError) as exc:
        raise LifecycleError("无法验证 Hermes Desktop 进程身份") from exc
    if not raw_marker.startswith("winms:"):
        raise LifecycleError("Hermes Desktop 缺少创建时间身份标记")
    try:
        marker_seconds = int(raw_marker[6:]) / 1000.0
    except ValueError as exc:
        raise LifecycleError("Hermes Desktop 创建时间身份标记无效") from exc
    if abs(marker_seconds - created) >= 0.01:
        raise LifecycleError("Hermes Desktop 进程身份已变化")
    return DesktopIdentity(expected_pid, created)
