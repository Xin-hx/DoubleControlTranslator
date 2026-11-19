"""Serialize clipboard metafiles without retaining clipboard-owned handles."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

import win32clipboard
import win32con


class METAFILEPICT(ctypes.Structure):
    _fields_ = [("mm", ctypes.c_int), ("xExt", ctypes.c_int),
                ("yExt", ctypes.c_int), ("hMF", wintypes.HANDLE)]


def _apis():
    kernel, gdi = ctypes.windll.kernel32, ctypes.windll.gdi32
    for name, result, args in (
        ("GlobalAlloc", wintypes.HANDLE, (wintypes.UINT, ctypes.c_size_t)),
        ("GlobalLock", ctypes.c_void_p, (wintypes.HANDLE,)),
        ("GlobalSize", ctypes.c_size_t, (wintypes.HANDLE,)),
        ("GlobalUnlock", wintypes.BOOL, (wintypes.HANDLE,)),
        ("GlobalFree", wintypes.HANDLE, (wintypes.HANDLE,)),
    ):
        method = getattr(kernel, name)
        method.restype, method.argtypes = result, args
    gdi.GetMetaFileBitsEx.argtypes = (wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p)
    gdi.GetMetaFileBitsEx.restype = wintypes.UINT
    for name in ("SetMetaFileBitsEx", "SetEnhMetaFileBits"):
        method = getattr(gdi, name)
        method.argtypes = (wintypes.UINT, ctypes.c_void_p)
        method.restype = wintypes.HANDLE
    for name in ("DeleteMetaFile", "DeleteEnhMetaFile"):
        method = getattr(gdi, name)
        method.argtypes, method.restype = (wintypes.HANDLE,), wintypes.BOOL
    return kernel, gdi


@dataclass(frozen=True)
class ClipboardMetafile:
    bits: bytes
    extent: tuple[int, int, int] | None = None

    @classmethod
    def capture(cls, fmt: int) -> "ClipboardMetafile":
        if fmt == win32con.CF_ENHMETAFILE:
            return cls(win32clipboard.GetClipboardData(fmt))
        kernel, gdi = _apis()
        handle = win32clipboard.GetClipboardDataHandle(fmt)
        if kernel.GlobalSize(handle) < ctypes.sizeof(METAFILEPICT):
            raise OSError("剪贴板矢量图结构不完整")
        address = kernel.GlobalLock(handle)
        if not address:
            raise OSError("无法锁定剪贴板矢量图")
        try:
            picture = METAFILEPICT.from_address(address)
            size = gdi.GetMetaFileBitsEx(picture.hMF, 0, None)
            if not size:
                raise OSError("无法读取剪贴板矢量图长度")
            bits = ctypes.create_string_buffer(size)
            if gdi.GetMetaFileBitsEx(picture.hMF, size, bits) != size:
                raise OSError("无法读取剪贴板矢量图")
            return cls(bits.raw, (picture.mm, picture.xExt, picture.yExt))
        finally:
            kernel.GlobalUnlock(handle)

    def create_handle(self) -> int:
        kernel, gdi = _apis()
        if self.extent is None:
            handle = gdi.SetEnhMetaFileBits(len(self.bits), self.bits)
            if not handle:
                raise OSError("无法重建剪贴板增强矢量图")
            return int(handle)
        metafile = gdi.SetMetaFileBitsEx(len(self.bits), self.bits)
        if not metafile:
            raise OSError("无法重建剪贴板矢量图")
        handle = kernel.GlobalAlloc(0x0042, ctypes.sizeof(METAFILEPICT))  # GHND
        address = kernel.GlobalLock(handle) if handle else None
        if not address:
            if handle:
                kernel.GlobalFree(handle)
            gdi.DeleteMetaFile(metafile)
            raise OSError("无法分配剪贴板矢量图内存")
        try:
            picture = METAFILEPICT(*self.extent, metafile)
            ctypes.memmove(address, ctypes.byref(picture), ctypes.sizeof(picture))
        finally:
            kernel.GlobalUnlock(handle)
        return int(handle)

    def free_handle(self, handle: int) -> None:
        kernel, gdi = _apis()
        if self.extent is None:
            gdi.DeleteEnhMetaFile(handle)
        else:
            address = kernel.GlobalLock(handle)
            if address:
                try:
                    gdi.DeleteMetaFile(METAFILEPICT.from_address(address).hMF)
                finally:
                    kernel.GlobalUnlock(handle)
            kernel.GlobalFree(handle)
