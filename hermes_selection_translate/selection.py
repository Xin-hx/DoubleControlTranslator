from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import queue
import threading
import time

import win32clipboard
import win32con


class SelectionError(RuntimeError):
    pass


def _open_clipboard(timeout_s: float = 0.8) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            win32clipboard.OpenClipboard()
            return
        except Exception as exc:
            if time.monotonic() >= deadline:
                raise SelectionError("剪贴板正被其他程序占用，已取消") from exc
            time.sleep(0.01)


@dataclass(frozen=True)
class WindowIdentity:
    hwnd: int
    pid: int
    created_100ns: int


def window_identity(hwnd: int | None = None) -> WindowIdentity:
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetForegroundWindow.argtypes = ()
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    if hwnd is None:
        hwnd = int(user32.GetForegroundWindow())
    if not hwnd:
        raise SelectionError("没有可用的前台窗口")
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME))
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        raise SelectionError("无法确认前台窗口进程身份")
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    try:
        if not kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            raise SelectionError("无法确认前台窗口创建时间")
    finally:
        kernel32.CloseHandle(handle)
    stamp = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
    return WindowIdentity(int(hwnd), int(pid.value), stamp)


def verify_foreground(expected: WindowIdentity) -> None:
    current = window_identity()
    if current != expected:
        raise SelectionError("前台窗口已变化，已取消")


class ClipboardSnapshot:
    """A copy of every clipboard format pywin32 can safely materialize.

    CF_BITMAP is preserved through the equivalent system-synthesized DIB. All
    other integer values remain opaque handles and fail closed rather than
    risking invalid ownership or clipboard corruption.
    """

    def __init__(self, items: list[tuple[int, object]], sequence: int):
        self.items = items
        self.sequence = sequence

    @classmethod
    def capture(cls) -> "ClipboardSnapshot":
        for _ in range(3):
            items: list[tuple[int, object]] = []
            bitmap_seen = False
            dib_seen = False
            _open_clipboard()
            try:
                fmt = 0
                while True:
                    fmt = win32clipboard.EnumClipboardFormats(fmt)
                    if not fmt:
                        break
                    if fmt == win32con.CF_BITMAP:
                        bitmap_seen = True
                        continue
                    try:
                        value = win32clipboard.GetClipboardData(fmt)
                    except Exception as exc:
                        raise SelectionError("剪贴板含无法安全保存的格式，已取消") from exc
                    if isinstance(value, int) or not isinstance(value, (str, bytes, bytearray, tuple, list)):
                        raise SelectionError("剪贴板含 GDI 或无法安全恢复的格式，已取消")
                    if isinstance(value, bytearray):
                        value = bytes(value)
                    elif isinstance(value, list):
                        value = tuple(value)
                    items.append((int(fmt), value))
                    if fmt in (win32con.CF_DIB, win32con.CF_DIBV5):
                        dib_seen = True
                if bitmap_seen and not dib_seen:
                    raise SelectionError("剪贴板含无法安全恢复的 GDI 位图，已取消")
                sequence = int(win32clipboard.GetClipboardSequenceNumber())
            finally:
                win32clipboard.CloseClipboard()
            # Synthesized formats can commit on CloseClipboard. Recapture so
            # the bytes and the sequence always describe the same state.
            time.sleep(.02)
            if int(win32clipboard.GetClipboardSequenceNumber()) == sequence:
                return cls(items, sequence)
        raise SelectionError("剪贴板内容持续变化，已取消")

    def restore_if_unchanged(self, copied_sequence: int | None) -> bool:
        if copied_sequence is None:
            return True
        if int(win32clipboard.GetClipboardSequenceNumber()) != copied_sequence:
            return False
        _open_clipboard()
        try:
            # Recheck while owning the clipboard so an external update cannot
            # slip between the identity check and EmptyClipboard.
            if int(win32clipboard.GetClipboardSequenceNumber()) != copied_sequence:
                return False
            win32clipboard.EmptyClipboard()
            for fmt, value in self.items:
                win32clipboard.SetClipboardData(fmt, value)
        finally:
            win32clipboard.CloseClipboard()
        return True


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value: str) -> "GUID":
        import uuid
        u = uuid.UUID(value)
        raw = u.bytes_le
        return cls.from_buffer_copy(raw)


class BRECORD(ctypes.Structure):
    _fields_ = [("pvRecord", ctypes.c_void_p), ("pRecInfo", ctypes.c_void_p)]


class DECIMAL(ctypes.Structure):
    _fields_ = [("wReserved", wintypes.WORD), ("scale", ctypes.c_ubyte),
                ("sign", ctypes.c_ubyte), ("Hi32", wintypes.ULONG),
                ("Lo64", ctypes.c_ulonglong)]


class _VariantValue(ctypes.Union):
    _fields_ = [("llVal", ctypes.c_longlong), ("lVal", wintypes.LONG),
                ("boolVal", ctypes.c_short), ("ptr", ctypes.c_void_p),
                ("brecVal", BRECORD)]


class _VariantTagged(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("vt", wintypes.WORD), ("wReserved1", wintypes.WORD),
                ("wReserved2", wintypes.WORD), ("wReserved3", wintypes.WORD),
                ("value", _VariantValue)]


class VARIANT(ctypes.Union):
    _anonymous_ = ("tagged",)
    _fields_ = [("tagged", _VariantTagged), ("decVal", DECIMAL)]


HRESULT = ctypes.c_long
COMFUN = ctypes.WINFUNCTYPE
UIA_IS_PASSWORD = 30019
UIA_TEXT_PATTERN = 10014
UIA_VALUE_PATTERN = 10002
VT_BOOL = 11
VARIANT_TRUE = -1
UIA_E_NOTSUPPORTED = ctypes.c_long(0x80040200).value


def _method(ptr: ctypes.c_void_p, index: int, restype, *argtypes):
    address = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents[index]
    return COMFUN(restype, ctypes.c_void_p, *argtypes)(address)


def _release(ptr: ctypes.c_void_p | None) -> None:
    if ptr and ptr.value:
        _method(ptr, 2, wintypes.ULONG)(ptr)


def _check(hr: int, message: str) -> None:
    if hr < 0:
        raise OSError(hr, message)


def _property_int(element: ctypes.c_void_p, property_id: int) -> int | None:
    value = VARIANT()
    try:
        hr = _method(element, 10, HRESULT, ctypes.c_int, ctypes.POINTER(VARIANT))(
            element, property_id, ctypes.byref(value))
        _check(hr, "读取 UI Automation 控件属性失败")
        return int(value.lVal) if value.vt == 3 else None  # VT_I4
    finally:
        ctypes.windll.oleaut32.VariantClear(ctypes.byref(value))


def _classic_edit_selection(element: ctypes.c_void_p) -> str | None:
    """Typed UIA ValuePattern + bounded native range for the stock Win32 Edit.

    The Windows UIA proxy exposes ValuePattern, but not TextPattern, for the
    classic EDIT class. UIA still supplies the password decision and value;
    EM_GETSEL supplies only the two range offsets with SendMessageTimeout.
    """
    if _property_int(element, 30003) != 50004:  # UIA_EditControlTypeId
        return None
    hwnd = _property_int(element, 30020)  # UIA_NativeWindowHandlePropertyId
    if not hwnd:
        return None
    unknown = ctypes.c_void_p()
    pattern = ctypes.c_void_p()
    bstr = ctypes.c_void_p()
    try:
        hr = _method(element, 16, HRESULT, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
            element, UIA_VALUE_PATTERN, ctypes.byref(unknown))
        if hr == UIA_E_NOTSUPPORTED or not unknown.value:
            return None
        _check(hr, "读取 Win32 Edit 值接口失败")
        iid_value = GUID.parse("a94cd8b1-0844-4cd6-9d2d-640537ab39e9")
        hr = _method(unknown, 0, HRESULT, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
            unknown, ctypes.byref(iid_value), ctypes.byref(pattern))
        _check(hr, "Win32 Edit 值接口类型不受支持")
        hr = _method(pattern, 4, HRESULT, ctypes.POINTER(ctypes.c_void_p))(
            pattern, ctypes.byref(bstr))
        _check(hr, "读取 Win32 Edit 文本失败")
        full = ctypes.wstring_at(bstr, ctypes.windll.oleaut32.SysStringLen(bstr)) if bstr.value else ""
        result = ctypes.c_size_t()
        user32 = ctypes.windll.user32
        user32.SendMessageTimeoutW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                               wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                               ctypes.POINTER(ctypes.c_size_t))
        user32.SendMessageTimeoutW.restype = wintypes.LPARAM
        if not user32.SendMessageTimeoutW(hwnd, 0x00B0, 0, 0, 0x0002, 200, ctypes.byref(result)):
            raise SelectionError("读取 Win32 Edit 选区超时")
        start, end = result.value & 0xFFFF, (result.value >> 16) & 0xFFFF
        if end < start or end > len(full):
            return None
        selected = full[start:end].strip()
        return selected or None
    finally:
        if bstr.value:
            ctypes.windll.oleaut32.SysFreeString(bstr)
        _release(pattern)
        _release(unknown)


def _uia_selection_sync(expected: WindowIdentity | None = None) -> tuple[str | None, bool]:
    """Return (text, safe_to_use_clipboard_fallback) via typed COM vtables."""
    ole32, oleaut32 = ctypes.windll.ole32, ctypes.windll.oleaut32
    ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    ole32.CoInitializeEx.restype = HRESULT
    ole32.CoCreateInstance.argtypes = (ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
    ole32.CoCreateInstance.restype = HRESULT
    ole32.CoUninitialize.argtypes = ()
    ole32.CoUninitialize.restype = None
    oleaut32.VariantClear.argtypes = (ctypes.POINTER(VARIANT),)
    oleaut32.VariantClear.restype = HRESULT
    oleaut32.SysStringLen.argtypes = (ctypes.c_void_p,)
    oleaut32.SysStringLen.restype = wintypes.UINT
    oleaut32.SysFreeString.argtypes = (ctypes.c_void_p,)
    oleaut32.SysFreeString.restype = None
    COINIT_APARTMENTTHREADED = 2
    initialized = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED) >= 0
    if not initialized:
        raise SelectionError("UI Automation 初始化失败；为保护密码框不使用剪贴板回退")
    automation = ctypes.c_void_p()
    element = ctypes.c_void_p()
    pattern = ctypes.c_void_p()
    ranges = ctypes.c_void_p()
    try:
        clsid = GUID.parse("ff48dba4-60ef-4201-aa87-54103eef594e")
        iid_uia = GUID.parse("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 1,
                                    ctypes.byref(iid_uia), ctypes.byref(automation))
        _check(hr, "创建 UI Automation 客户端失败")
        hr = _method(automation, 8, HRESULT, ctypes.POINTER(ctypes.c_void_p))(
            automation, ctypes.byref(element))
        _check(hr, "读取焦点控件失败")
        if expected is not None:
            if _property_int(element, 30002) != expected.pid:  # UIA_ProcessIdPropertyId
                raise SelectionError("UI Automation 焦点控件不属于触发窗口")
            native_hwnd = _property_int(element, 30020)
            if native_hwnd:
                user32 = ctypes.windll.user32
                user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
                user32.GetAncestor.restype = wintypes.HWND
                if int(user32.GetAncestor(native_hwnd, 2) or 0) != expected.hwnd:  # GA_ROOT
                    raise SelectionError("UI Automation 控件窗口身份已变化")

        value = VARIANT()
        hr = _method(element, 10, HRESULT, ctypes.c_int, ctypes.POINTER(VARIANT))(
            element, UIA_IS_PASSWORD, ctypes.byref(value))
        try:
            _check(hr, "读取密码属性失败")
            if value.vt != VT_BOOL:
                raise SelectionError("无法确认控件密码属性；为保护内容不使用剪贴板回退")
            if value.boolVal == VARIANT_TRUE:
                raise SelectionError("密码输入框不读取选区")
        finally:
            oleaut32.VariantClear(ctypes.byref(value))

        # Get the generalized IUnknown then perform an explicit typed QI. This
        # works with native providers that return a raw COM interface rather
        # than an Automation/IDispatch object (the original review blocker).
        unknown_pattern = ctypes.c_void_p()
        hr = _method(element, 16, HRESULT, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
            element, UIA_TEXT_PATTERN, ctypes.byref(unknown_pattern))
        if hr == UIA_E_NOTSUPPORTED:
            return _classic_edit_selection(element), True
        _check(hr, "读取文本选区接口失败")
        if not unknown_pattern.value:
            return _classic_edit_selection(element), True
        iid_text = GUID.parse("32eba289-3583-42c9-9c59-3b6d9a1e9b6a")
        try:
            hr = _method(unknown_pattern, 0, HRESULT, ctypes.POINTER(GUID),
                         ctypes.POINTER(ctypes.c_void_p))(
                unknown_pattern, ctypes.byref(iid_text), ctypes.byref(pattern))
            _check(hr, "文本选区接口类型不受支持")
        finally:
            _release(unknown_pattern)
        hr = _method(pattern, 5, HRESULT, ctypes.POINTER(ctypes.c_void_p))(
            pattern, ctypes.byref(ranges))
        _check(hr, "读取 UI Automation 选区失败")
        length = ctypes.c_int()
        hr = _method(ranges, 3, HRESULT, ctypes.POINTER(ctypes.c_int))(
            ranges, ctypes.byref(length))
        _check(hr, "读取 UI Automation 选区数量失败")
        texts: list[str] = []
        for index in range(max(0, length.value)):
            text_range = ctypes.c_void_p()
            bstr = ctypes.c_void_p()
            try:
                hr = _method(ranges, 4, HRESULT, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                    ranges, index, ctypes.byref(text_range))
                _check(hr, "读取 UI Automation 选区范围失败")
                hr = _method(text_range, 12, HRESULT, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                    text_range, -1, ctypes.byref(bstr))
                _check(hr, "读取 UI Automation 选区文本失败")
                if bstr.value:
                    size = int(oleaut32.SysStringLen(bstr))
                    texts.append(ctypes.wstring_at(bstr, size))
            finally:
                if bstr.value:
                    oleaut32.SysFreeString(bstr)
                _release(text_range)
        result = "\n".join(part for part in texts if part).strip()
        return result or None, True
    except SelectionError:
        raise
    except OSError as exc:
        raise SelectionError("UI Automation 读取失败；为保护密码框不使用剪贴板回退") from exc
    finally:
        _release(ranges)
        _release(pattern)
        _release(element)
        _release(automation)
        ole32.CoUninitialize()


_uia_pending = threading.Lock()


def _uia_probe(timeout_s: float = 0.75, expected: WindowIdentity | None = None) -> tuple[str | None, bool]:
    if not _uia_pending.acquire(blocking=False):
        raise SelectionError("已有 UI Automation 读取仍在进行，已取消")
    result: queue.Queue = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            result.put((True, _uia_selection_sync(expected)))
        except BaseException as exc:
            result.put((False, exc))
        finally:
            _uia_pending.release()

    thread = threading.Thread(target=run, name="uia-selection", daemon=True)
    thread.start()
    try:
        ok, value = result.get(timeout=timeout_s)
    except queue.Empty as exc:
        raise SelectionError("UI Automation 读取超时；为保护密码框不使用剪贴板回退") from exc
    if not ok:
        if isinstance(value, SelectionError):
            raise value
        raise SelectionError("UI Automation 读取失败；为保护密码框不使用剪贴板回退") from value
    return value


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", INPUTUNION)]


COPY_INPUT_MARKER = 0x4853545243505931


def _send_ctrl_c() -> None:
    user32 = ctypes.windll.user32
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    KEYEVENTF_KEYUP = 2

    def key(vk: int, flags: int = 0) -> INPUT:
        return INPUT(type=1, ki=KEYBDINPUT(vk, 0, flags, 0, COPY_INPUT_MARKER))

    events = (INPUT * 4)(key(0x11), key(0x43), key(0x43, KEYEVENTF_KEYUP),
                         key(0x11, KEYEVENTF_KEYUP))
    sent = user32.SendInput(len(events), events, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise SelectionError("无法发送复制按键")


def _clipboard_window_matches(expected: WindowIdentity, owner: int) -> bool:
    user32 = ctypes.windll.user32
    if not owner:
        return False
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsWindow.restype = wintypes.BOOL
    if not user32.IsWindow(owner):
        return False
    try:
        owner_identity = window_identity(owner)
    except SelectionError:
        return False
    if (owner_identity.pid, owner_identity.created_100ns) != (expected.pid, expected.created_100ns):
        return False
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    root = int(user32.GetAncestor(owner, 2) or owner)
    if root == expected.hwnd:
        return True
    # OLE and browser engines may put the clipboard on a same-process hidden
    # top-level broker instead of the visible document HWND.  Limit this
    # exception to that exact process lifetime and to a genuinely hidden root;
    # a second visible document window in the process remains untrusted.
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    return root == owner and not bool(user32.IsWindowVisible(owner))


def _clipboard_owner_matches(expected: WindowIdentity) -> bool:
    user32 = ctypes.windll.user32
    user32.GetClipboardOwner.argtypes = ()
    user32.GetClipboardOwner.restype = wintypes.HWND
    return _clipboard_window_matches(expected, int(user32.GetClipboardOwner() or 0))


_selection_lock = threading.Lock()


def clipboard_fallback(expected: WindowIdentity, cancel: threading.Event | None = None) -> str:
    user32 = ctypes.windll.user32
    user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    user32.GetAsyncKeyState.restype = ctypes.c_short
    deadline = time.monotonic() + 0.8
    while user32.GetAsyncKeyState(0x11) & 0x8000:
        if cancel and cancel.is_set():
            raise SelectionError("翻译已取消")
        if time.monotonic() >= deadline:
            raise SelectionError("Ctrl 尚未释放")
        time.sleep(0.01)
    verify_foreground(expected)
    snapshot = ClipboardSnapshot.capture()
    copied_sequence: int | None = None
    try:
        verify_foreground(expected)
        _send_ctrl_c()
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            verify_foreground(expected)
            sequence = int(win32clipboard.GetClipboardSequenceNumber())
            if sequence != snapshot.sequence:
                if not _clipboard_owner_matches(expected):
                    # This was an unrelated application update, not the copy
                    # we requested. Never read it as selection and never put
                    # the old snapshot over it.
                    raise SelectionError("复制期间剪贴板被其他程序更新，已取消且未覆盖")
                if cancel and cancel.is_set():
                    raise SelectionError("翻译已取消")
                _open_clipboard()
                try:
                    # Attribute again while holding the clipboard. This closes
                    # the check/open race. Delayed rendering may advance the
                    # sequence while the same valid owner supplies the data.
                    current_sequence = int(win32clipboard.GetClipboardSequenceNumber())
                    if not _clipboard_owner_matches(expected):
                        raise SelectionError("读取期间剪贴板来源已变化，已取消且未覆盖")
                    # Only an attributed sequence may authorize restoration.
                    copied_sequence = current_sequence
                    if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                        raise SelectionError("新选区不是文本")
                    text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT).strip()
                    latest_sequence = int(win32clipboard.GetClipboardSequenceNumber())
                    if not _clipboard_owner_matches(expected):
                        raise SelectionError("读取期间剪贴板来源已变化，已取消且未覆盖")
                    copied_sequence = latest_sequence
                finally:
                    win32clipboard.CloseClipboard()
                verify_foreground(expected)
                if not text:
                    raise SelectionError("选区为空")
                return text
            if cancel and cancel.is_set():
                raise SelectionError("翻译已取消")
            time.sleep(0.02)
        raise SelectionError("未检测到新的剪贴板内容")
    finally:
        snapshot.restore_if_unchanged(copied_sequence)


def uia_selected_text(timeout_s: float = 0.75) -> str | None:
    return _uia_probe(timeout_s)[0]


def get_selected_text(max_chars: int, expected: WindowIdentity | None = None,
                      cancel: threading.Event | None = None, *, prefer_uia: bool = True) -> str:
    if cancel and cancel.is_set():
        raise SelectionError("翻译已取消")
    with _selection_lock:
        if cancel and cancel.is_set():
            raise SelectionError("翻译已取消")
        expected = expected or window_identity()
        verify_foreground(expected)
        text: str | None = None
        safe_fallback = True
        if prefer_uia:
            text, safe_fallback = _uia_probe(expected=expected)
            verify_foreground(expected)
            if cancel and cancel.is_set():
                raise SelectionError("翻译已取消")
        if text is None:
            if not safe_fallback:
                raise SelectionError("无法安全读取选区")
            text = clipboard_fallback(expected, cancel)
        if len(text) > max_chars:
            raise SelectionError(f"选区超过 {max_chars} 字符上限")
        return text


# Compatibility name for external integration scripts. It intentionally uses
# the same bounded and fail-closed implementation as get_selected_text.
def get_selection() -> str:
    return get_selected_text(2000)
