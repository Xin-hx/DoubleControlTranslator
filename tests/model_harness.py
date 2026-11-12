from __future__ import annotations

from pathlib import Path
import os
import socket
import subprocess
import time
import urllib.request

import psutil


ROOT = Path(__file__).resolve().parents[1]


class TemporaryLlama:
    def __init__(self, port: int = 18436):
        self.port = port
        self.process = None
        self.created = None
        self.log_handle = None

    @property
    def endpoint(self):
        return {"base_url": f"http://127.0.0.1:{self.port}/v1", "api_key": ""}

    def __enter__(self):
        home = Path(os.environ.get("HERMES_HOME", Path(os.environ["LOCALAPPDATA"]) / "hermes"))
        binary = home / "runtimes" / "llamacpp" / "b10679" / "cuda" / "llama-server.exe"
        model = home / "models" / "Qwen3.5-4B-Q4_K_M.gguf"
        if not binary.is_file() or not model.is_file():
            raise RuntimeError("existing llama binary/model missing")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", self.port))
        log_dir = ROOT / "runtime"; log_dir.mkdir(exist_ok=True)
        self.log_handle = (log_dir / "verification-llama.log").open("a", encoding="utf-8")
        args = [str(binary), "--model", str(model), "--host", "127.0.0.1",
                "--port", str(self.port), "--ctx-size", "4096", "--parallel", "1",
                "--jinja", "--reasoning", "off", "--no-webui"]
        self.process = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL,
                                        stdout=self.log_handle, stderr=subprocess.STDOUT,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                        shell=False)
        self.created = psutil.Process(self.process.pid).create_time()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("temporary llama exited during load")
            try:
                with opener.open(f"http://127.0.0.1:{self.port}/health", timeout=1) as response:
                    if response.status == 200: return self
            except OSError:
                pass
            time.sleep(.25)
        raise RuntimeError("temporary llama load timeout")

    def __exit__(self, exc_type, exc, tb):
        process = self.process
        try:
            identity_ok = bool(process and self.created is not None and
                               abs(psutil.Process(process.pid).create_time() - self.created) < .01)
        except (psutil.Error, OSError):
            identity_ok = False
        if process and process.poll() is None and identity_ok:
            process.terminate()
            try: process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(5)
        if self.log_handle: self.log_handle.close()
