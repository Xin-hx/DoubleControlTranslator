"""Real Win32 regression for handle-free CF_BITMAP snapshot/restore.

The pre-test user clipboard is retained only in memory and conditionally
restored. No clipboard payload is printed or written to disk.
"""
from __future__ import annotations

import json
import ctypes
from ctypes import wintypes
from pathlib import Path
import secrets
import struct
import sys
import time

import win32clipboard
import win32con

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from double_ctrl_translator.selection import ClipboardSnapshot
from double_ctrl_translator.clipboard_metafile import ClipboardMetafile


PUBLIC_PIXELS = bytes.fromhex(
    # bottom row: blue, white; top row: red, green (BGRA)
    "ff000000 ffffff00 0000ff00 00ff0000"
)
PUBLIC_DIB = struct.pack(
    "<IiiHHIIiiII", 40, 2, 2, 1, 32, 0, len(PUBLIC_PIXELS), 0, 0, 0, 0
) + PUBLIC_PIXELS
PUBLIC_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc000000401010018dd8db10000000049454e44ae426082"
)


def set_public_image(png_format: int, owner_format: int, owner_token: bytes) -> int:
    gdi = ctypes.windll.gdi32
    gdi.CreateEnhMetaFileW.argtypes = (wintypes.HDC, wintypes.LPCWSTR, ctypes.POINTER(wintypes.RECT), wintypes.LPCWSTR)
    gdi.CreateEnhMetaFileW.restype = wintypes.HDC
    gdi.Rectangle.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int)
    gdi.CloseEnhMetaFile.argtypes, gdi.CloseEnhMetaFile.restype = (wintypes.HDC,), wintypes.HANDLE
    gdi.DeleteEnhMetaFile.argtypes = (wintypes.HANDLE,)
    dc = gdi.CreateEnhMetaFileW(None, None, None, None)
    assert dc
    gdi.Rectangle(dc, 0, 0, 20, 20)
    metafile = gdi.CloseEnhMetaFile(dc)
    assert metafile
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        # Install the ownership marker first so a later fixture setup failure
        # can still be distinguished from an external clipboard replacement.
        win32clipboard.SetClipboardData(owner_format, owner_token)
        win32clipboard.SetClipboardData(win32con.CF_DIB, PUBLIC_DIB)
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, "Hermes public fixture")
        win32clipboard.SetClipboardData(png_format, PUBLIC_PNG)
        win32clipboard.SetClipboardData(win32con.CF_ENHMETAFILE, int(metafile))
        metafile = None
    finally:
        win32clipboard.CloseClipboard()
        if metafile:
            gdi.DeleteEnhMetaFile(metafile)
    return int(win32clipboard.GetClipboardSequenceNumber())


def fixture_sequence_if_still_owned(owner_format: int, owner_token: bytes) -> int | None:
    """Accept delayed-render sequence changes without inspecting foreign data."""
    for _ in range(5):
        win32clipboard.OpenClipboard()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(owner_format):
                return None
            if win32clipboard.GetClipboardData(owner_format) != owner_token:
                return None
            sequence = int(win32clipboard.GetClipboardSequenceNumber())
        finally:
            win32clipboard.CloseClipboard()
        time.sleep(.02)
        if int(win32clipboard.GetClipboardSequenceNumber()) == sequence:
            return sequence
    return None


def main() -> None:
    backup = ClipboardSnapshot.capture()
    fixture_sequence: int | None = None
    fixture_started = False
    restored_user = False
    try:
        png_format = win32clipboard.RegisterClipboardFormat("PNG")
        owner_format = win32clipboard.RegisterClipboardFormat("DoubleCtrlTranslator.GDIFixture.Owner")
        owner_token = secrets.token_bytes(32)
        fixture_started = True
        fixture_sequence = set_public_image(png_format, owner_format, owner_token)

        win32clipboard.OpenClipboard()
        try:
            bitmap_available = bool(win32clipboard.IsClipboardFormatAvailable(win32con.CF_BITMAP))
            bitmap_handle = win32clipboard.GetClipboardData(win32con.CF_BITMAP)
        finally:
            win32clipboard.CloseClipboard()
        assert bitmap_available and isinstance(bitmap_handle, int) and bitmap_handle

        snapshot = ClipboardSnapshot.capture()
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, "temporary selection")
        finally:
            win32clipboard.CloseClipboard()
        copied_sequence = int(win32clipboard.GetClipboardSequenceNumber())
        assert snapshot.restore_if_unchanged(copied_sequence)
        fixture_sequence = int(win32clipboard.GetClipboardSequenceNumber())

        win32clipboard.OpenClipboard()
        try:
            restored_dib = win32clipboard.GetClipboardData(win32con.CF_DIB)
            restored_png = win32clipboard.GetClipboardData(png_format)
            restored_bitmap = win32clipboard.GetClipboardData(win32con.CF_BITMAP)
            restored_text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        assert restored_dib == PUBLIC_DIB
        assert restored_dib[40:] == PUBLIC_PIXELS
        assert restored_png == PUBLIC_PNG
        assert isinstance(restored_bitmap, int) and restored_bitmap
        assert restored_text == "Hermes public fixture"
        after = ClipboardSnapshot.capture()
        original_metafiles = [(fmt, value) for fmt, value in snapshot.items if isinstance(value, ClipboardMetafile)]
        restored_metafiles = [(fmt, value) for fmt, value in after.items if isinstance(value, ClipboardMetafile)]
        assert original_metafiles == restored_metafiles
        assert {fmt for fmt, _ in original_metafiles} == {win32con.CF_METAFILEPICT, win32con.CF_ENHMETAFILE}

        # A later update cannot be overwritten by a stale transaction.
        stale_sequence = fixture_sequence
        set_public_image(png_format, owner_format, owner_token)
        fixture_sequence = ClipboardSnapshot.capture().sequence
        assert not snapshot.restore_if_unchanged(stale_sequence)

        evidence = {
            "ok": True,
            "bitmap_handle_seen": True,
            "dib_pixel_bytes": len(PUBLIC_PIXELS),
            "png_preserved": True,
            "wmf_emf_preserved": True,
            "stale_restore_refused": True,
            "snapshot_format_ids": [fmt for fmt, _ in snapshot.items],
        }
    finally:
        owned_sequence = (fixture_sequence_if_still_owned(owner_format, owner_token)
                          if fixture_started else None)
        if owned_sequence is not None:
            restored_user = backup.restore_if_unchanged(owned_sequence)
        if fixture_started and not restored_user:
            print(json.dumps({"user_clipboard_restored": False, "reason": "sequence_changed",
                              "expected_sequence": owned_sequence,
                              "actual_sequence": int(win32clipboard.GetClipboardSequenceNumber())}))
        elif fixture_started:
            evidence["user_clipboard_restored"] = True
            print(json.dumps(evidence))


if __name__ == "__main__":
    main()
