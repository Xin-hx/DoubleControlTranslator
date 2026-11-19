from __future__ import annotations

import psutil


Identity = tuple[int, float]


def identity(pid: int) -> Identity | None:
    try:
        process = psutil.Process(pid)
        return process.pid, process.create_time()
    except (psutil.Error, OSError):
        return None


def owned_tree(root_pid: int, root_created: float) -> list[Identity]:
    """Snapshot only the validated root and its current descendants."""
    try:
        root = psutil.Process(root_pid)
        if abs(root.create_time() - root_created) >= 0.01:
            return []
        processes = [root, *root.children(recursive=True)]
        return [(process.pid, process.create_time()) for process in processes]
    except (psutil.Error, OSError):
        return []


def stop_owned(identities: list[Identity], terminate_timeout: float = 3.0) -> None:
    """Stop a captured tree without ever acting on a recycled PID."""
    live: list[psutil.Process] = []
    for pid, created in reversed(identities):
        try:
            process = psutil.Process(pid)
            if abs(process.create_time() - created) < 0.01:
                live.append(process)
        except (psutil.Error, OSError):
            pass
    for process in live:
        try:
            process.terminate()
        except psutil.Error:
            pass
    _, survivors = psutil.wait_procs(live, timeout=terminate_timeout)
    for process in survivors:
        try:
            if any(process.pid == pid and abs(process.create_time() - created) < 0.01
                   for pid, created in identities):
                process.kill()
        except (psutil.Error, OSError):
            pass
    if survivors:
        psutil.wait_procs(survivors, timeout=min(2.0, terminate_timeout))
