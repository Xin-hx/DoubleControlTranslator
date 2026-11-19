"""Start an isolated project-owned llama, translate a public sentence, clean it up."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from double_ctrl_translator.model import stream_translate, warmup
from model_harness import TemporaryLlama

started = time.perf_counter(); chunks = []
with TemporaryLlama() as llama:
    load_s = time.perf_counter() - started
    warm = warmup(llama.endpoint, "Qwen3.5-4B-Q4_K_M.gguf", threading.Event(), 90)
    timings = []
    for _ in range(2):
        translate_started = time.perf_counter()
        result = stream_translate("The quick brown fox jumps over the lazy dog.", llama.endpoint,
                                  "Qwen3.5-4B-Q4_K_M.gguf", chunks.append,
                                  threading.Event(), 20, 3, 90)
        timings.append(time.perf_counter() - translate_started)
        assert any("\u4e00" <= char <= "\u9fff" for char in result)
print(json.dumps({"ok": True, "load_s": round(load_s, 3),
                  "warmup_s": round(warm["warmup_ms"] / 1000, 3),
                  "warmup_first_token_s": round(warm["warmup_first_token_ms"] / 1000, 3),
                  "first_translate_s": round(timings[0], 3),
                  "repeat_translate_s": round(timings[1], 3), "chunks": len(chunks),
                  "result_is_chinese": True, "process_cleaned": True}, ensure_ascii=False))
