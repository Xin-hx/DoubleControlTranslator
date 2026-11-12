from __future__ import annotations

import ctypes
from ctypes import wintypes
import queue
import threading

from .gesture import DoubleCtrlDetector
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
LLKHF_INJECTED = 0x10
E2E_INPUT_MARKER = 0x4853545245324531


class KeyboardHook:
    def __init__(self, interval_ms: int, triggers: queue.Queue, escapes: queue.Queue | None = None,
                 *, allow_test_marker: bool = False):
        self.detector = DoubleCtrlDetector(interval_ms)
        self.triggers = triggers
        self.escapes = escapes
        self.allow_test_marker = allow_test_marker
        self.thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.thread_id = 0
        self.error: str | None = None
        self._stats_lock = threading.Lock()
        self._stats = {"keyboard_events": 0, "pure_ctrl_taps": 0,
                       "double_ctrl_triggers": 0}

    def diagnostics(self) -> dict:
        with self._stats_lock:
            return {**self._stats, "hook_healthy": bool(self.thread and self.ready.is_set() and
                                                        not self.error)}

    def start(self) -> None:
        if self.thread:
            return
        self.thread = threading.Thread(target=self._run, name="double-ctrl-hook", daemon=True)
        self.thread.start()
        if not self.ready.wait(3):
            raise RuntimeError("键盘钩子启动超时")
        if self.error:
            self.thread = None
            raise RuntimeError(self.error)

    def stop(self) -> None:
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x12, 0, 0)
        if self.thread:
            self.thread.join(3)
            self.thread = None
        self.thread_id = 0

    def _run(self) -> None:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        user32.SetWindowsHookExW.argtypes = (ctypes.c_int, ctypes.c_void_p,
                                             wintypes.HINSTANCE, wintypes.DWORD)
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int,
                                          wintypes.WPARAM, ctypes.c_void_p)
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        self.thread_id = int(kernel32.GetCurrentThreadId())

        class KBD(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("extra", ctypes.c_size_t)]

        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                           wintypes.WPARAM, ctypes.c_void_p)

        def callback(code, msg, ptr):
            if code >= 0:
                data = ctypes.cast(ptr, ctypes.POINTER(KBD)).contents
                down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
                injected = bool(data.flags & LLKHF_INJECTED)
                accepted_test_input = (self.allow_test_marker and injected and
                                       int(data.extra) == E2E_INPUT_MARKER)
                if down and data.vkCode == 0x1B and not injected and self.escapes is not None:
                    try:
                        self.escapes.put_nowait(True)
                    except queue.Full:
                        pass
                modifier_down = False
                if data.vkCode in self.detector.CTRL_KEYS and down:
                    for vk in (0x10, 0x12, 0x5B, 0x5C):  # Shift, Alt, Win
                        if user32.GetAsyncKeyState(vk) & 0x8000:
                            modifier_down = True
                            break
                trusted = not injected or accepted_test_input
                was_ctrl_down = int(data.vkCode) in self.detector.ctrl_down
                triggered = self.detector.feed(int(data.vkCode), down, int(data.time),
                                               injected and not accepted_test_input, modifier_down)
                with self._stats_lock:
                    self._stats["keyboard_events"] += 1
                    if (trusted and not down and was_ctrl_down and
                            not self.detector.ctrl_down and self.detector.clean):
                        self._stats["pure_ctrl_taps"] += 1
                    if triggered:
                        self._stats["double_ctrl_triggers"] += 1
                if triggered:
                    point = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(point))
                    try:
                        # HWND capture is cheap. PID/create-time identity is deliberately
                        # resolved by the worker thread, never inside the global hook.
                        hwnd = int(user32.GetForegroundWindow())
                        self.triggers.put_nowait((int(point.x), int(point.y), hwnd))
                    except queue.Full:
                        pass
            # Returning CallNextHookEx unconditionally means Escape and every
            # ordinary key continue to the foreground application.
            return user32.CallNextHookEx(None, code, msg, ptr)

        self._callback = callback_type(callback)
        hook = user32.SetWindowsHookExW(13, self._callback, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            self.error = "键盘钩子安装失败"
            self.ready.set()
            return
        self.ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        user32.UnhookWindowsHookEx(hook)
