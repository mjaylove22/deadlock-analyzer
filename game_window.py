"""Find the Deadlock window, so capture works on any monitor and in windowed mode.

Windows are matched by the program that owns them (deadlock.exe), not by title. Only window
positions are read, through standard Windows calls: nothing touches the game itself.
"""

import ctypes
import os
from ctypes import wintypes
from typing import Optional, Tuple

GAME_EXE = "deadlock.exe"
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

Box = Tuple[int, int, int, int]  # left, top, right, bottom in screen pixels

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _exe_of(hwnd) -> str:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if _kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value).lower()
        return ""
    finally:
        _kernel32.CloseHandle(process)


def client_area(hwnd) -> Box:
    """The window's content area (no title bar or borders), in screen pixels."""
    rect = wintypes.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(rect))
    corner = wintypes.POINT(0, 0)
    _user32.ClientToScreen(hwnd, ctypes.byref(corner))
    return corner.x, corner.y, corner.x + rect.right, corner.y + rect.bottom


def find_window(exe: str = GAME_EXE) -> Optional[Box]:
    """The client area of the largest visible, non-minimised window owned by exe, or None."""
    found = []

    def check(hwnd, _):
        if _user32.IsWindowVisible(hwnd) and not _user32.IsIconic(hwnd) and _exe_of(hwnd) == exe.lower():
            box = client_area(hwnd)
            if box[2] - box[0] > 100 and box[3] - box[1] > 100:  # skip tiny helper windows
                found.append(box)
        return True

    _user32.EnumWindows(_WNDENUMPROC(check), 0)
    return max(found, key=lambda b: (b[2] - b[0]) * (b[3] - b[1])) if found else None
