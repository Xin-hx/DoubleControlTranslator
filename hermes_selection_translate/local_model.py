from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
import threading

import psutil

from .settings import Settings
from .launch import child_command, child_environment
from .process_tree import owned_tree, stop_owned


class LocalModelError(RuntimeError):
    pass


class OwnedModel:
    """Own a guard process which in turn owns exactly one llama-server child."""

    def __init__(self, project_root: Path, port: int = 18435):
        self.project_root = project_root.resolve()
        self.port = port
        self.process: subprocess.Popen | None = None
        self.created: float | None = None
        self.server_pid: int | None = None
        self.lock = threading.RLock()
        self.ready = False
        self.loading = False
        self.generation = 0
        self.owned: list[tuple[int, float]] = []

    @property
    def endpoint(self) -> dict:
        return {"base_url": f"http://127.0.0.1:{self.port}/v1", "api_key": "",
                "model": "Qwen3.5-4B-Q4_K_M.gguf", "model_state": "loaded"}

    def _identity_matches(self) -> bool:
        if not self.process or self.created is None:
            return False
        try:
            return abs(psutil.Process(self.process.pid).create_time() - self.created) < 0.01
        except (psutil.Error, OSError):
            return False

    def running(self) -> bool:
        with self.lock:
            return bool(self.ready and self.process and self.process.poll() is None and self._identity_matches())

    def start(self, owner, load_timeout: float = 180.0, cancel=None) -> dict:
        with self.lock:
            if cancel is not None and cancel.is_set():
                raise LocalModelError("模型加载已取消")
            if self.running():
                return self.endpoint
            if self.process is not None:
                self._stop_locked()
            if not owner.alive():
                raise LocalModelError("独立程序已退出，拒绝启动模型")
            with socket.socket() as sock:
                try:
                    sock.bind(("127.0.0.1", self.port))
                except OSError as exc:
                    raise LocalModelError(f"项目模型端口 {self.port} 已被占用") from exc
            model, runtime = Settings.load().resources()
            if not model or not model.is_file() or not runtime or not runtime.is_file():
                raise LocalModelError("模型或 llama-server 缺失，请在设置中选择文件")
            args = child_command("model-guard",
                    "--desktop-pid", str(owner.pid), "--desktop-created", str(owner.created),
                    "--project-root", str(self.project_root), "--port", str(self.port),
                    "--runtime", str(runtime), "--model", str(model),
                    "--load-timeout", str(load_timeout))
            env = child_environment()
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                args, cwd=self.project_root, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                creationflags=flags, shell=False,
            )
            created = psutil.Process(process.pid).create_time()
            self.generation += 1
            generation = self.generation
            self.process = process
            self.created = created
            self.ready = False
            self.loading = True
            self.owned = owned_tree(process.pid, created)
            result: list[dict] = []
            finished = threading.Event()

            def read_ready() -> None:
                try:
                    result.append(json.loads(process.stdout.readline()))
                except Exception:
                    result.append({"ready": False})
                finally:
                    finished.set()

            reader = threading.Thread(target=read_ready, name="model-guard-ready", daemon=True)
            reader.start()
        finished.wait(load_timeout + 10)
        with self.lock:
            current = generation == self.generation and process is self.process
            success = (current and result and result[0].get("ready") is True and
                       process.poll() is None)
            if success:
                server_pid = result[0].get("pid")
                descendants = {pid for pid, _ in owned_tree(process.pid, created)}
                self.server_pid = server_pid if isinstance(server_pid, int) and server_pid in descendants else None
                self.ready = True
                self.loading = False
                self.owned = owned_tree(process.pid, created)
                return self.endpoint
            message = result[0].get("error") if result else "项目翻译模型加载超时"
            if current:
                self._stop_locked()
            if not current:
                raise LocalModelError("项目翻译模型加载已取消")
            raise LocalModelError(message or "项目翻译模型启动失败")

    def stop(self) -> None:
        with self.lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        process = self.process
        identity_ok = self._identity_matches()
        owned = list(self.owned)
        if process and identity_ok:
            owned = owned_tree(process.pid, self.created) or owned
        self.generation += 1
        self.process = None
        self.created = None
        self.server_pid = None
        self.ready = False
        self.loading = False
        self.owned = []
        if process and process.poll() is None and identity_ok:
            try:
                process.stdin.write('{"op":"stop"}\n')
                process.stdin.flush()
                process.wait(12)
            except Exception:
                if process.poll() is None:
                    try:
                        process.terminate()
                        process.wait(4)
                    except Exception:
                        pass
        stop_owned(owned)

    def _clear(self) -> None:
        self.process = None
        self.created = None
        self.server_pid = None
        self.ready = False
        self.loading = False
        self.owned = []
