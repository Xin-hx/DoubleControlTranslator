from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import shutil
import threading

from .config import Config


def data_dir() -> Path:
    return Path(os.environ.get("DOUBLECTRLTRANSLATOR_HOME", Path(os.environ["LOCALAPPDATA"]) / "DoubleCtrlTranslator"))


def default_hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path(os.environ["LOCALAPPDATA"]) / "hermes"))


@dataclass
class Settings:
    hermes_home: str = field(default_factory=lambda: str(default_hermes_home()))
    model_path: str = ""
    runtime_path: str = ""
    launch_with_hermes: bool = True
    listening: bool = False
    translation: dict = field(default_factory=dict)

    @classmethod
    def load(cls, directory: Path | None = None) -> "Settings":
        migrate = directory is None and "DOUBLECTRLTRANSLATOR_HOME" not in os.environ
        directory = directory or data_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "settings.json"
        previous = Path(os.environ["LOCALAPPDATA"]) / "HermesTool" / "settings.json"
        if not path.exists() and migrate and previous.is_file():
            shutil.copy2(previous, path)
        if path.exists():
            raw = json.loads(path.read_text("utf-8"))
            result = cls(**raw)
            if not all(isinstance(v, str) for v in (result.hermes_home, result.model_path, result.runtime_path)):
                raise ValueError("资源路径必须是字符串")
            if not isinstance(result.listening, bool) or not isinstance(result.launch_with_hermes, bool):
                raise ValueError("开关设置无效")
            Config(**result.translation).validate()
            return result
        result = cls()
        old = Path(result.hermes_home) / "plugins/selection-translate/config.json"
        if old.is_file():
            config = Config.load(old)
            result.translation = asdict(config)
            shutil.copy2(old, directory / "legacy-config.backup.json")
        result.save(directory)
        return result

    def save(self, directory: Path | None = None) -> None:
        directory = directory or data_dir()
        directory.mkdir(parents=True, exist_ok=True)
        # Atomic replacement: interrupted writes never destroy the previous settings.
        temp = directory / ("settings.%s.%s.tmp" % (os.getpid(), threading.get_ident()))
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), "utf-8")
        temp.replace(directory / "settings.json")

    def resources(self) -> tuple[Path | None, Path | None]:
        home = Path(self.hermes_home)
        model = Path(self.model_path) if self.model_path else home / "models/Qwen3.5-4B-Q4_K_M.gguf"
        if self.runtime_path:
            runtime = Path(self.runtime_path)
        else:
            candidates = list((home / "runtimes/llamacpp").glob("*/*/llama-server.exe"))
            candidates.sort(key=lambda p: (p.stat().st_mtime, str(p)), reverse=True)
            runtime = candidates[0] if candidates else None
        return model, runtime
