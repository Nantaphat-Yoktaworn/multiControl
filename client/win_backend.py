"""
Windows Native Backend using Win32 ctypes (user32, shcore, kernel32).

Responsibilities:
  1. Enable Per-Monitor DPI Awareness V2 so multi-monitor coordinates and
     SetCursorPos / SendInput are 100% pixel-accurate regardless of Windows scaling.
  2. Enumerate all attached Windows monitors via EnumDisplayMonitors + GetMonitorInfoW.
  3. Inject mouse movements, clicks, smooth horizontal/vertical scrolling, and
     keyboard events (with hardware scan codes) via Win32 SendInput.
  4. Automatically release held modifiers/buttons and park cursor on LEAVE_SCREEN.
  5. Read/write Windows Unicode clipboard via native Win32 Clipboard API.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import sys
from typing import List, Set, Tuple

from layout_engine import DisplayRect

logger = logging.getLogger("multicontrol.win")

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    try:
        shcore = ctypes.windll.shcore
    except Exception:
        shcore = None

    # Enable Per-Monitor DPI Awareness V2 (-4) before any display API calls
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            if shcore is not None:
                shcore.SetProcessDpiAwareness(2)
            else:
                user32.SetProcessDPIAware()
        except Exception:
            pass

    # Win32 Constants
    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1

    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_XDOWN = 0x0080
    MOUSEEVENTF_XUP = 0x0100
    MOUSEEVENTF_WHEEL = 0x0800
    MOUSEEVENTF_HWHEEL = 0x01000
    MOUSEEVENTF_VIRTUALDESK = 0x4000
    MOUSEEVENTF_ABSOLUTE = 0x8000

    XBUTTON1 = 0x0001
    XBUTTON2 = 0x0002
    WHEEL_DELTA = 120

    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    MAPVK_VK_TO_VSC = 0

    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    ULONG_PTR = ctypes.WINTYPE_ULONG_PTR if hasattr(ctypes, "WINTYPE_ULONG_PTR") else ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUTunion(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [
            ("type", wintypes.DWORD),
            ("union", _INPUTunion),
        ]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    # Clipboard ctypes signatures
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE


def get_win_displays(device_id: str, device_name: str) -> List[DisplayRect]:
    """Enumerate all attached Windows monitors with true pixel bounds and DPI scale."""
    if not IS_WINDOWS:
        return [
            DisplayRect(
                id=f"{device_id}:0",
                device_id=device_id,
                device_name=device_name,
                os_type="windows",
                monitor_index=0,
                name="Windows Display 1",
                primary=True,
                os_x=0.0,
                os_y=0.0,
                os_width=1920.0,
                os_height=1080.0,
                scale_factor=1.0,
                canvas_x=1920.0,
                canvas_y=0.0,
                canvas_width=1920.0,
                canvas_height=1080.0,
            )
        ]

    raw_monitors: List[Tuple[int, int, int, int, bool, str, float]] = []

    def _callback(hmonitor, hdc, lprect, lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            left = int(info.rcMonitor.left)
            top = int(info.rcMonitor.top)
            width = int(info.rcMonitor.right - info.rcMonitor.left)
            height = int(info.rcMonitor.bottom - info.rcMonitor.top)
            is_primary = bool(info.dwFlags & 1)
            dev_str = str(info.szDevice).replace("\\\\.\\", "")

            scale = 1.0
            if shcore is not None:
                try:
                    dpi_x = wintypes.UINT(96)
                    dpi_y = wintypes.UINT(96)
                    if shcore.GetDpiForMonitor(
                        hmonitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
                    ) == 0:
                        scale = round(float(dpi_x.value) / 96.0, 2)
                except Exception:
                    pass

            raw_monitors.append((left, top, width, height, is_primary, dev_str, scale))
        return True

    enum_proc = MONITORENUMPROC(_callback)
    user32.EnumDisplayMonitors(None, None, enum_proc, 0)

    # Sort so primary comes first, then left-to-right
    raw_monitors.sort(key=lambda m: (0 if m[4] else 1, m[0], m[1]))

    displays: List[DisplayRect] = []
    for idx, (left, top, width, height, is_primary, dev_str, scale) in enumerate(raw_monitors):
        label = f"Windows Main ({dev_str})" if is_primary else f"Windows Display {idx + 1} ({dev_str})"
        displays.append(
            DisplayRect(
                id=f"{device_id}:{idx}",
                device_id=device_id,
                device_name=device_name,
                os_type="windows",
                monitor_index=idx,
                name=label,
                primary=is_primary,
                os_x=float(left),
                os_y=float(top),
                os_width=float(width),
                os_height=float(height),
                scale_factor=scale,
                canvas_x=float(left),
                canvas_y=float(top),
                canvas_width=float(width),
                canvas_height=float(height),
            )
        )

    return displays


def get_win_clipboard() -> str:
    """Read Unicode text from Windows clipboard."""
    if not IS_WINDOWS:
        return ""
    text = ""
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if handle:
            ptr = kernel32.GlobalLock(handle)
            if ptr:
                try:
                    text = ctypes.wstring_at(ptr)
                finally:
                    kernel32.GlobalUnlock(handle)
    except Exception:
        pass
    finally:
        user32.CloseClipboard()
    return text


def set_win_clipboard(text: str) -> None:
    """Write Unicode text to Windows clipboard."""
    if not IS_WINDOWS or text is None:
        return
    if not user32.OpenClipboard(None):
        return
    try:
        user32.EmptyClipboard()
        encoded = text.encode("utf-16-le") + b"\x00\x00"
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if h_mem:
            ptr = kernel32.GlobalLock(h_mem)
            if ptr:
                ctypes.memmove(ptr, encoded, len(encoded))
                kernel32.GlobalUnlock(h_mem)
                user32.SetClipboardData(CF_UNICODETEXT, h_mem)
    except Exception:
        pass
    finally:
        user32.CloseClipboard()


class WinInputInjector:
    """Injects low-latency mouse and keyboard events on Windows via Win32 SendInput."""

    def __init__(self) -> None:
        self._held_keys: Set[Tuple[int, int]] = set()
        self._held_buttons: Set[int] = set()
        self._last_x: int = 0
        self._last_y: int = 0

    def _virtual_screen_bounds(self) -> Tuple[int, int, int, int]:
        if not IS_WINDOWS:
            return (0, 0, 1920, 1080)
        vx = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        vy = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        vw = max(1, int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)))
        vh = max(1, int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)))
        return vx, vy, vw, vh

    def move_cursor(self, x: int, y: int) -> None:
        """Move Windows cursor to exact OS pixel coordinate `(x, y)`."""
        self._last_x = int(x)
        self._last_y = int(y)
        if not IS_WINDOWS:
            return

        # Normalize to [0..65535] across the entire Windows Virtual Desktop
        vx, vy, vw, vh = self._virtual_screen_bounds()
        norm_x = int(round(((x - vx) * 65535.0) / max(1, vw - 1)))
        norm_y = int(round(((y - vy) * 65535.0) / max(1, vh - 1)))
        norm_x = max(0, min(65535, norm_x))
        norm_y = max(0, min(65535, norm_y))

        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.union.mi = MOUSEINPUT(
            dx=norm_x,
            dy=norm_y,
            mouseData=0,
            dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            time=0,
            dwExtraInfo=0,
        )
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def mouse_button(self, button: int, state: int) -> None:
        """Inject mouse button press (state=1) or release (state=0)."""
        if not IS_WINDOWS:
            return

        flags = 0
        mouse_data = 0
        is_down = bool(state == 1)

        if button == 1:
            flags = MOUSEEVENTF_LEFTDOWN if is_down else MOUSEEVENTF_LEFTUP
        elif button == 2:
            flags = MOUSEEVENTF_RIGHTDOWN if is_down else MOUSEEVENTF_RIGHTUP
        elif button == 3:
            flags = MOUSEEVENTF_MIDDLEDOWN if is_down else MOUSEEVENTF_MIDDLEUP
        elif button == 4:
            flags = MOUSEEVENTF_XDOWN if is_down else MOUSEEVENTF_XUP
            mouse_data = XBUTTON1
        elif button == 5:
            flags = MOUSEEVENTF_XDOWN if is_down else MOUSEEVENTF_XUP
            mouse_data = XBUTTON2
        else:
            return

        if is_down:
            self._held_buttons.add(button)
        else:
            self._held_buttons.discard(button)

        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.union.mi = MOUSEINPUT(
            dx=0,
            dy=0,
            mouseData=mouse_data,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        )
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def mouse_scroll(
        self,
        dx: float,
        dy: float,
        is_pixel: bool,
        scroll_speed: float = 1.0,
        invert: bool = False,
    ) -> None:
        """Inject vertical and horizontal scroll events on Windows."""
        if not IS_WINDOWS:
            return

        direction = -1.0 if invert else 1.0
        mult = (4.0 if is_pixel else float(WHEEL_DELTA)) * scroll_speed * direction

        wheel_y = int(round(dy * mult))
        # On Windows MOUSEEVENTF_HWHEEL positive tilts right, macOS axis2 positive is left
        wheel_x = int(round(-dx * mult))

        inputs = []
        if wheel_y != 0:
            inp_y = INPUT()
            inp_y.type = INPUT_MOUSE
            inp_y.union.mi = MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=ctypes.c_ulong(wheel_y & 0xFFFFFFFF).value,
                dwFlags=MOUSEEVENTF_WHEEL,
                time=0,
                dwExtraInfo=0,
            )
            inputs.append(inp_y)

        if wheel_x != 0:
            inp_x = INPUT()
            inp_x.type = INPUT_MOUSE
            inp_x.union.mi = MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=ctypes.c_ulong(wheel_x & 0xFFFFFFFF).value,
                dwFlags=MOUSEEVENTF_HWHEEL,
                time=0,
                dwExtraInfo=0,
            )
            inputs.append(inp_x)

        if inputs:
            arr = (INPUT * len(inputs))(*inputs)
            user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))

    def key_event(self, vk_code: int, state: int, flags: int = 0) -> None:
        """Inject keyboard key press (state=1) or release (state=0) using VK + ScanCode."""
        if not IS_WINDOWS:
            return

        is_down = bool(state == 1)
        scan_code = int(user32.MapVirtualKeyW(vk_code, MAPVK_VK_TO_VSC))
        dw_flags = 0
        if flags & 1:
            dw_flags |= KEYEVENTF_EXTENDEDKEY
        if not is_down:
            dw_flags |= KEYEVENTF_KEYUP

        if is_down:
            self._held_keys.add((vk_code, flags))
        else:
            self._held_keys.discard((vk_code, flags))

        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.union.ki = KEYBDINPUT(
            wVk=vk_code,
            wScan=scan_code,
            dwFlags=dw_flags,
            time=0,
            dwExtraInfo=0,
        )
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def release_all(self) -> None:
        """Release any held keys or mouse buttons to prevent stuck modifiers."""
        for btn in list(self._held_buttons):
            try:
                self.mouse_button(btn, 0)
            except Exception:
                pass
        self._held_buttons.clear()

        for vk, flags in list(self._held_keys):
            try:
                self.key_event(vk, 0, flags)
            except Exception:
                pass
        self._held_keys.clear()

    def leave_screen(self) -> None:
        """Called when cursor transitions from Windows back to Mac."""
        self.release_all()
        if IS_WINDOWS:
            # Tuck Windows cursor discreetly at bottom-right corner so it doesn't clutter the screen
            vx, vy, vw, vh = self._virtual_screen_bounds()
            self.move_cursor(vx + vw - 2, vy + vh - 2)
