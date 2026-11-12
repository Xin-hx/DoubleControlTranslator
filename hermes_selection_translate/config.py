from __future__ import annotations

from dataclasses import dataclass, fields
import json
from pathlib import Path


@dataclass(frozen=True)
class Config:
    double_ctrl_ms: int = 350
    max_chars: int = 2000
    connect_timeout_s: float = 3.0
    read_timeout_s: float = 20.0
    total_timeout_s: float = 90.0
    model: str = "Qwen3.5-4B-Q4_K_M.gguf"

    @classmethod
    def load(cls, path: Path | None) -> "Config":
        raw = json.loads(path.read_text("utf-8")) if path and path.exists() else {}
        allowed = {f.name for f in fields(cls)}
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise ValueError("配置包含未知字段")
        cfg = cls(**raw)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        cfg = self
        if (not 150 <= cfg.double_ctrl_ms <= 1000 or not 1 <= cfg.max_chars <= 10000 or
                not 0.2 <= cfg.connect_timeout_s <= 15 or
                not 1 <= cfg.read_timeout_s <= 120 or
                not cfg.read_timeout_s <= cfg.total_timeout_s <= 300 or
                cfg.model != Config.model):
            raise ValueError("配置值超出安全范围")
