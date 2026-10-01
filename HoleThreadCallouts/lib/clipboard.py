"""Put an image on the clipboard: as PNG (Office, browsers, most apps) and as a DIB (everything
else, e.g. Paint). Windows only; on a Mac the image is only saved to a file."""

import ctypes
import sys

CF_DIB = 8
GMEM_MOVEABLE = 0x0002


def copy_image(png, bmp=None):
    """png: PNG file bytes; bmp: optional BMP file bytes of the same image (for CF_DIB)."""
    if sys.platform != "win32":
        return False
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.RegisterClipboardFormatW.restype = ctypes.c_uint

    def put(fmt, data):
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(fmt, handle):
            raise OSError("couldn't set the clipboard")

    if not user32.OpenClipboard(None):
        raise OSError("clipboard is busy")
    try:
        user32.EmptyClipboard()
        put(user32.RegisterClipboardFormatW("PNG"), png)
        if bmp and bmp[:2] == b"BM":
            put(CF_DIB, bmp[14:])               # a DIB is a BMP file without its 14-byte file header
    finally:
        user32.CloseClipboard()
    return True
