"""Map the hook's physical cursor point to the matching Qt logical screen."""
import ctypes
from ctypes import wintypes


class MONITORINFOEX(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32)]


def logical_point(x, y, screen):
    user = ctypes.windll.user32
    user.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user.MonitorFromPoint.restype = wintypes.HMONITOR
    monitor = user.MonitorFromPoint(wintypes.POINT(x, y), 2)
    info = MONITORINFOEX(cbSize=ctypes.sizeof(MONITORINFOEX))
    if user.GetMonitorInfoW(monitor, ctypes.byref(info)) and info.szDevice == screen.name():
        rect = screen.geometry()
        ratio = screen.devicePixelRatio()
        return round(rect.x() + (x - info.rcMonitor.left) / ratio), round(rect.y() + (y - info.rcMonitor.top) / ratio)
    return None
