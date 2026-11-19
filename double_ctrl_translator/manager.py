from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
import os
from pathlib import Path
import subprocess
import threading
import time

import psutil

from .config import Config
from .endpoint import resolve_healthy_endpoint
from .launch import child_command, child_environment
from .local_model import OwnedModel
from .model import warmup
from .process_tree import owned_tree, stop_owned
from .settings import Settings, data_dir


@dataclass(frozen=True)
class AppIdentity:
    pid: int
    created: float

    def alive(self):
        try:
            return abs(psutil.Process(self.pid).create_time() - self.created) < .01
        except psutil.Error:
            return False


class WorkerManager:
    """Standalone coordinator. Only owned processes are stopped; shared models are read-only."""
    def __init__(self, package_root: Path | None = None):
        self.root = data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.process = None
        self.process_created = None
        self.worker_log = None
        self.model = OwnedModel(self.root)
        self.owner = AppIdentity(os.getpid(), psutil.Process().create_time())
        self.endpoint = None
        self.source = "none"
        self.phase = "disabled"
        self.error = None
        self.notice = None
        self.diagnostics = {}
        self.generation = 0
        self.cancel = threading.Event()
        self.want_enabled = False
        self.busy = False
        self.model_suspended = False
        self.last_probe = 0.0
        self.load_ms = self.warmup_ms = self.warmup_first_token_ms = None

    def _worker_running(self):
        try:
            return bool(self.process and self.process.poll() is None and
                        abs(psutil.Process(self.process.pid).create_time() - self.process_created) < .01)
        except psutil.Error:
            return False

    def _send(self, value):
        if self._worker_running():
            try:
                self.process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
                return True
            except (OSError, ValueError):
                self.error = "监听进程通信失败"
        return False

    def _set_endpoint(self, endpoint, source):
        if endpoint != self.endpoint:
            self._send({"op": "endpoint", "endpoint": endpoint})
        self.endpoint, self.source = endpoint, source

    def _messages(self, process):
        try:
            for line in process.stdout:
                try:
                    value = json.loads(line)
                except ValueError:
                    logging.warning("Ignored non-JSON worker diagnostic")
                    continue
                if value.get("type") == "diagnostics":
                    with self.lock:
                        if process is self.process:
                            self.diagnostics = value
        except (OSError, ValueError):
            logging.exception("Worker diagnostics pipe closed")

    def _start_worker(self):
        if self._worker_running():
            return
        config = Config(**Settings.load().translation)
        config.validate()
        path = self.root / "worker-config.json"
        path.write_text(json.dumps(asdict(config)), "utf-8")
        logs = self.root / "logs"
        logs.mkdir(exist_ok=True)
        self.worker_log = (logs / "worker.log").open("a", encoding="utf-8")
        self.process = subprocess.Popen(child_command("worker", "--owner-pid", str(self.owner.pid),
            "--owner-created", str(self.owner.created), "--config", str(path)),
            env=child_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.worker_log, text=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
        process = self.process
        self.process_created = psutil.Process(process.pid).create_time()
        ready = []
        def read_ready():
            try:
                ready.append(json.loads(process.stdout.readline()))
            except (ValueError, OSError):
                ready.append({})
        reader = threading.Thread(target=read_ready, daemon=True)
        reader.start()
        reader.join(8)
        if not ready or ready[0].get("ready") is not True or not self._worker_running():
            self._stop_worker()
            raise RuntimeError("监听进程启动失败，请查看 logs/worker.log")
        threading.Thread(target=self._messages, args=(process,), daemon=True).start()

    def enable_and_load(self):
        with self.lock:
            if self.busy:
                return self.status()
            self.busy = True
            self.want_enabled = True
            self.model_suspended = False
            self.generation += 1
            generation = self.generation
            self.cancel = threading.Event()
            cancel = self.cancel
            self.error = None
            self.notice = None
            self.phase = "starting_model"
        try:
            with self.lock:
                self._start_worker()
            if self.model.running():
                endpoint, source = self.model.endpoint, "project"
            else:
                try:
                    endpoint, source = resolve_healthy_endpoint(), "shared"
                except RuntimeError:
                    if cancel.is_set():
                        return self.status()
                    started = time.monotonic()
                    endpoint, source = self.model.start(self.owner, cancel=cancel), "project"
                    self.load_ms = int((time.monotonic() - started) * 1000)
            with self.lock:
                if generation != self.generation or cancel.is_set():
                    return self.status()
                self._set_endpoint(endpoint, source)
                self.phase = "warming_model"
            timing = warmup(endpoint, endpoint["model"], cancel)
            with self.lock:
                if generation == self.generation and not cancel.is_set():
                    self.warmup_ms = timing["warmup_ms"]
                    self.warmup_first_token_ms = timing["warmup_first_token_ms"]
                    self.phase = "running"
        except Exception as exc:
            with self.lock:
                if generation == self.generation and not cancel.is_set():
                    self.error = str(exc)
                    self.phase = "error"
        finally:
            with self.lock:
                self.busy = False
        return self.status()

    def status(self):
        recover = False
        with self.lock:
            running = self._worker_running()
            if self.want_enabled and not running and not self.busy:
                self.phase = "error"
                self.error = "监听进程已退出，请重新开启"
            if running and not self.busy and time.monotonic() - self.last_probe > 2:
                self.last_probe = time.monotonic()
                previous = self.source
                if self.model.running():
                    endpoint, source = self.model.endpoint, "project"
                else:
                    try:
                        endpoint, source = resolve_healthy_endpoint(), "shared"
                    except RuntimeError:
                        endpoint, source = None, "none"
                self._set_endpoint(endpoint, source)
                if endpoint is None and previous in {"shared", "project"} and not self.model_suspended:
                    self.notice = "模型连接已断开，本次翻译已取消；正在准备独立模型，请稍后重新划词。"
                    recover = self.want_enabled
                    self.phase = "starting_model" if recover else "needs_model"
                elif endpoint is None and not self.error:
                    self.phase = "needs_model"
                elif endpoint and not self.error:
                    self.phase = "running"
            self._send({"op": "status"})
            result = {"enabled": running, "state": self.phase, "model": self.source,
                      "model_readiness": "warming" if self.phase == "warming_model" else
                          "loading" if self.phase == "starting_model" else
                          "loaded" if self.endpoint else "unavailable",
                      "error": self.error, "notice": self.notice,
                      "diagnostics": dict(self.diagnostics), "load_ms": self.load_ms,
                      "warmup_ms": self.warmup_ms, "warmup_first_token_ms": self.warmup_first_token_ms}
            for key, process in (("worker", self.process), ("model", self.model.process)):
                if process and process.poll() is None:
                    result[key + "_pid"] = (self.model.server_pid or process.pid) if key == "model" else process.pid
                    try:
                        result[key + "_rss_bytes"] = psutil.Process(result[key + "_pid"]).memory_info().rss
                    except psutil.Error:
                        pass
        if recover:
            threading.Thread(target=self.enable_and_load, daemon=True, name="model-recovery").start()
        return result

    def stop_model(self):
        with self.lock:
            self.generation += 1
            self.cancel.set()
            self.model_suspended = True
            self._send({"op": "cancel"})
            self._set_endpoint(None, "none")
        self.model.stop()
        with self.lock:
            self.phase = "needs_model" if self._worker_running() else "disabled"
            self.error = None
        return self.status()

    def _stop_worker(self):
        process = self.process
        owned = owned_tree(process.pid, self.process_created) if self._worker_running() else []
        self._send({"op": "stop"})
        self.process = None
        self.process_created = None
        if process:
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                stop_owned(owned)
        if self.worker_log:
            self.worker_log.close()
            self.worker_log = None
        self.diagnostics = {}

    def disable(self):
        with self.lock:
            self.want_enabled = False
            self.generation += 1
            self.cancel.set()
            self._stop_worker()
            self.endpoint = None
            self.source = "none"
            self.phase = "disabled"
            self.error = self.notice = None
        self.model.stop()
        return self.status()

    def test_card(self):
        with self.lock:
            accepted = bool(self.endpoint and self._send({"op": "test", "text": "The quick brown fox jumps over the lazy dog."}))
            return {**self.status(), "accepted": accepted}
