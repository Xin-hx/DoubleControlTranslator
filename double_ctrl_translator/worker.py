from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
import queue
import sys
import threading
import time
from PySide6.QtWidgets import QApplication
from .qt_ui import Dispatcher, apply_style

import psutil

from .card import TranslationCard
from .config import Config
from .hook import KeyboardHook
from .model import stream_translate, TranslationError, is_dictionary_query
from .selection import get_selected_text, SelectionError, WindowIdentity, window_identity


_ALLOWED_MODELS = {"Qwen3.5-4B-Q4_K_M", "Qwen3.5-4B-Q4_K_M.gguf"}


def request_model(endpoint: dict, configured: str) -> str:
    model = str(endpoint.get("model") or configured)
    if model not in _ALLOWED_MODELS:
        raise TranslationError("模型路由不是允许的 Qwen 4B")
    return model


class Worker:
    def __init__(self, config: Config, desktop_pid: int, desktop_created: float,
                 *, allow_test_marker: bool = False):
        self.config = config
        self.desktop_pid = desktop_pid
        self.desktop_created = desktop_created
        self.triggers: queue.Queue = queue.Queue(maxsize=1)
        self.escapes: queue.Queue = queue.Queue(maxsize=1)
        self.cancel = threading.Event()
        self.generation = 0
        self.request_lock = threading.Lock()
        self.endpoint_lock = threading.Lock()
        self.endpoint: dict | None = None
        self.stopped = False
        self.selection_failures = 0
        self.translation_failures = 0
        self.last_error: str | None = None
        self.last_first_token_ms: int | None = None
        self.last_request_ms: int | None = None
        self.app = QApplication.instance() or QApplication([])
        self.app.setQuitOnLastWindowClosed(False)
        apply_style(self.app)
        self.root = Dispatcher(self.app)
        self.card = TranslationCard(self.root, self._cancel)
        self.hook = KeyboardHook(config.double_ctrl_ms, self.triggers, self.escapes,
                                 allow_test_marker=allow_test_marker)

    def _cancel(self) -> None:
        with self.request_lock:
            self.cancel.set()
            self.generation += 1

    def pipe_reader(self) -> None:
        for line in sys.stdin:
            try:
                message = json.loads(line)
                operation = message.get("op")
                if operation == "stop":
                    self.root.after(0, self.stop)
                    return
                if operation == "endpoint":
                    endpoint = message.get("endpoint")
                    with self.endpoint_lock:
                        changed = self.endpoint != endpoint
                        self.endpoint = endpoint if isinstance(endpoint, dict) else None
                    if changed:
                        self._cancel()
                        self.root.after(0, lambda: self.card.update("模型连接已变化，请重新划词", "提示") if self.card.visible() else None)
                if operation == "cancel":
                    self._cancel()
                    self.root.after(0, self.card.close)
                if operation == "status":
                    self.root.after(0, self._emit_diagnostics)
                if operation == "test":
                    text = message.get("text")
                    if isinstance(text, str) and 0 < len(text) <= self.config.max_chars:
                        point = wintypes.POINT()
                        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
                        self.root.after(0, lambda t=text, x=point.x, y=point.y:
                                        self.translate(x, y, None, t))
                if operation == "test-selection" and self.hook.allow_test_marker:
                    hwnd = message.get("hwnd")
                    if isinstance(hwnd, int):
                        self.root.after(0, lambda h=hwnd: self.translate(160, 160, h))
            except Exception:
                continue
        self.root.after(0, self.stop)  # manager/backend died

    def desktop_alive(self) -> bool:
        try:
            process = psutil.Process(self.desktop_pid)
            return (abs(process.create_time() - self.desktop_created) < 0.01)
        except (psutil.Error, OSError):
            return False

    def poll(self) -> None:
        if self.stopped:
            return
        if not self.desktop_alive():
            self.stop()
            return
        try:
            self.escapes.get_nowait()
            if self.card.visible():
                self.card.close()
        except queue.Empty:
            pass
        try:
            x, y, hwnd = self.triggers.get_nowait()
            self.translate(x, y, hwnd)
        except queue.Empty:
            pass
        self.root.after(25, self.poll)

    def translate(self, x: int, y: int, target: WindowIdentity | int | None,
                  text_override: str | None = None) -> None:
        started = time.monotonic()
        with self.request_lock:
            self.cancel.set()
            request_cancel = threading.Event()
            self.cancel = request_cancel
            self.generation += 1
            generation = self.generation
        # Hook coordinates are native pixels; Qt positions use logical screen coordinates.
        from PySide6.QtGui import QGuiApplication
        from .win_coordinates import logical_point
        for candidate in QGuiApplication.screens():
            converted = logical_point(x, y, candidate)
            if converted is not None:
                x, y = converted
                break
        self.card.show(x, y, "读取选区 · 0.0 秒")

        def update(text: str, state: str = "success") -> None:
            if generation != self.generation or request_cancel.is_set() or self.stopped:
                return
            try:
                self.card.update(text, state)
            except RuntimeError:
                request_cancel.set()

        def post(callback) -> None:
            if self.stopped:
                return
            try:
                self.root.after(0, callback)
            except RuntimeError:
                request_cancel.set()

        def work() -> None:
            try:
                if request_cancel.is_set():
                    raise TranslationError("翻译已取消")
                text = text_override
                if text is None:
                    selection_target = window_identity(target) if isinstance(target, int) else target
                    text = get_selected_text(self.config.max_chars, selection_target, request_cancel)
                if request_cancel.is_set():
                    raise TranslationError("翻译已取消")
                dictionary = is_dictionary_query(text)
                elapsed = time.monotonic() - started
                remaining = self.config.total_timeout_s - elapsed
                if remaining <= 0:
                    raise TranslationError("翻译超过总时限")
                post(lambda e=elapsed: update(f"等待模型/首字 · {e:.1f} 秒", "progress"))
                with self.endpoint_lock:
                    endpoint = dict(self.endpoint) if self.endpoint else None
                if endpoint is None:
                    raise TranslationError("模型未就绪；请在主窗口检查环境或等待加载")

                first_token_at: list[float] = []
                def delta(full: str) -> None:
                    if not first_token_at:
                        first_token_at.append(time.monotonic())
                        self.last_first_token_ms = int((first_token_at[0] - started) * 1000)
                    elapsed_now = time.monotonic() - started
                    post(lambda value=full, e=elapsed_now:
                         update(f"生成{'词典内容' if dictionary else '译文'} · {e:.1f} 秒\n{value}", "progress"))

                result = stream_translate(
                    text, endpoint, request_model(endpoint, self.config.model), delta, request_cancel,
                    self.config.read_timeout_s, self.config.connect_timeout_s,
                    remaining, dictionary=dictionary,
                )
                self.last_request_ms = int((time.monotonic() - started) * 1000)
                post(lambda value=result: update(value))
            except (SelectionError, TranslationError, RuntimeError) as exc:
                if isinstance(exc, SelectionError):
                    self.selection_failures += 1
                else:
                    self.translation_failures += 1
                self.last_error = str(exc)
                self.last_request_ms = int((time.monotonic() - started) * 1000)
                post(lambda message=str(exc): update(message, "错误"))
            except Exception as exc:
                self.translation_failures += 1
                message = f"处理失败（{type(exc).__name__}）"
                self.last_error = message
                self.last_request_ms = int((time.monotonic() - started) * 1000)
                post(lambda value=message: update(value, "错误"))

        threading.Thread(target=work, name=f"translate-request-{generation}", daemon=True).start()

    def _emit_diagnostics(self) -> None:
        payload = {"type": "diagnostics", **self.hook.diagnostics(),
                   "selection_failures": self.selection_failures,
                   "translation_failures": self.translation_failures,
                   "last_error": self.last_error,
                   "last_first_token_ms": self.last_first_token_ms,
                   "last_request_ms": self.last_request_ms}
        if self.hook.allow_test_marker:
            # Explicit test mode only: let native acceptance observe the rendered result.
            payload["test_result"] = self.card.text
            payload["test_card_visible"] = self.card.visible()
        print(json.dumps(payload, ensure_ascii=False), flush=True)

    def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        self._cancel()
        self.hook.stop()
        try:
            self.root.destroy()
        except RuntimeError:
            pass

    def run(self) -> None:
        threading.Thread(target=self.pipe_reader, name="owner-pipe", daemon=True).start()
        self.hook.start()
        print(json.dumps({"ready": True, "pid": os.getpid()}), flush=True)
        self.root.after(25, self.poll)
        self.root.mainloop()


def main() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--desktop-pid", "--owner-pid", dest="desktop_pid", type=int, required=True)
    parser.add_argument("--desktop-created", "--owner-created", dest="desktop_created", type=float, required=True)
    parser.add_argument("--config")
    parser.add_argument("--allow-test-marker", action="store_true")
    args = parser.parse_args()
    config = Config.load(__import__("pathlib").Path(args.config) if args.config else None)
    Worker(config, args.desktop_pid, args.desktop_created,
           allow_test_marker=args.allow_test_marker).run()


if __name__ == "__main__":
    main()
