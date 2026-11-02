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
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

from layout_engine import DisplayRect

logger = logging.getLogger("multicontrol.win")

IS_WINDOWS = sys.platform == "win32"

user32 = None
kernel32 = None
advapi32 = None
shcore = None

if IS_WINDOWS:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    try:
        advapi32 = ctypes.windll.advapi32
    except Exception:
        advapi32 = None
    try:
        shcore = ctypes.windll.shcore
    except Exception:
        shcore = None

    # Enable Per-Monitor DPI Awareness V2 (-4) before any display API calls
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
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

    # Exact pointer-sized unsigned integer on 32-bit and 64-bit Windows
    ULONG_PTR = wintypes.WPARAM

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

    # Explicit Win32 ctypes signatures (prevents 64-bit HMONITOR/HANDLE truncation)
    user32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        MONITORENUMPROC,
        wintypes.LPARAM,
    ]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    user32.GetMonitorInfoW.argtypes = [
        wintypes.HMONITOR,
        ctypes.POINTER(MONITORINFOEXW),
    ]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    user32.MapVirtualKeyW.restype = wintypes.UINT
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT

    if shcore is not None:
        try:
            shcore.GetDpiForMonitor.argtypes = [
                wintypes.HMONITOR,
                ctypes.c_int,
                ctypes.POINTER(wintypes.UINT),
                ctypes.POINTER(wintypes.UINT),
            ]
            shcore.GetDpiForMonitor.restype = ctypes.c_long
        except Exception:
            pass

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

    # Desktop & Security Token ctypes structures
    class LUID(ctypes.Structure):
        _fields_ = [
            ("LowPart", wintypes.DWORD),
            ("HighPart", wintypes.LONG),
        ]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Luid", LUID),
            ("Attributes", wintypes.DWORD),
        ]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [
            ("PrivilegeCount", wintypes.DWORD),
            ("Privileges", LUID_AND_ATTRIBUTES * 1),
        ]

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    HDESK = wintypes.HANDLE

    # Desktop API signatures
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = HDESK
    user32.SetThreadDesktop.argtypes = [HDESK]
    user32.SetThreadDesktop.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = [HDESK]
    user32.CloseDesktop.restype = wintypes.BOOL
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetThreadDesktop.restype = HDESK
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetUserObjectInformationW.restype = wintypes.BOOL

    # Process & Token API signatures
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.GetLastError.argtypes = []
    kernel32.GetLastError.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.Process32NextW.restype = wintypes.BOOL

    if hasattr(kernel32, "ProcessIdToSessionId"):
        kernel32.ProcessIdToSessionId.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.ProcessIdToSessionId.restype = wintypes.BOOL

    if hasattr(kernel32, "WTSGetActiveConsoleSessionId"):
        kernel32.WTSGetActiveConsoleSessionId.argtypes = []
        kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD

    if advapi32 is not None:
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = wintypes.BOOL

        advapi32.LookupPrivilegeValueW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.POINTER(LUID),
        ]
        advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL

        advapi32.AdjustTokenPrivileges.argtypes = [
            wintypes.HANDLE,
            wintypes.BOOL,
            ctypes.POINTER(TOKEN_PRIVILEGES),
            wintypes.DWORD,
            ctypes.POINTER(TOKEN_PRIVILEGES),
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL

        advapi32.DuplicateTokenEx.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.DuplicateTokenEx.restype = wintypes.BOOL

        advapi32.ImpersonateLoggedOnUser.argtypes = [wintypes.HANDLE]
        advapi32.ImpersonateLoggedOnUser.restype = wintypes.BOOL

        advapi32.RevertToSelf.argtypes = []
        advapi32.RevertToSelf.restype = wintypes.BOOL


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
                    if (
                        shcore.GetDpiForMonitor(
                            hmonitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
                        )
                        == 0
                    ):
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
    for idx, (left, top, width, height, is_primary, dev_str, scale) in enumerate(
        raw_monitors
    ):
        label = (
            f"Windows Main ({dev_str})"
            if is_primary
            else f"Windows Display {idx + 1} ({dev_str})"
        )
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
        self._held_keys: Dict[int, int] = {}  # vk_code -> flags
        self._held_buttons: Set[int] = set()
        self._last_x: int = 0
        self._last_y: int = 0
        self._thread_local = threading.local()
        self._privilege_enabled = False
        self._access_denied_warned = False

    def _enable_se_debug_privilege(self) -> bool:
        """Enable SeDebugPrivilege for current process so it can query winlogon.exe token."""
        if not IS_WINDOWS or advapi32 is None:
            return False
        if self._privilege_enabled:
            return True
        try:
            h_token = wintypes.HANDLE()
            TOKEN_ADJUST_PRIVILEGES = 0x0020
            TOKEN_QUERY = 0x0008
            SE_PRIVILEGE_ENABLED = 0x00000002

            if not advapi32.OpenProcessToken(
                kernel32.GetCurrentProcess(),
                TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                ctypes.byref(h_token),
            ):
                return False

            try:
                luid = LUID()
                if not advapi32.LookupPrivilegeValueW(None, "SeDebugPrivilege", ctypes.byref(luid)):
                    return False

                tp = TOKEN_PRIVILEGES()
                tp.PrivilegeCount = 1
                tp.Privileges[0].Luid = luid
                tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED

                advapi32.AdjustTokenPrivileges(
                    h_token, False, ctypes.byref(tp), ctypes.sizeof(tp), None, None
                )
                ok = kernel32.GetLastError() == 0
                if ok:
                    self._privilege_enabled = True
                    logger.debug("Successfully enabled SeDebugPrivilege.")
                return ok
            finally:
                kernel32.CloseHandle(h_token)
        except Exception as exc:
            logger.debug("Failed enabling SeDebugPrivilege: %s", exc)
            return False

    def _impersonate_winlogon(self) -> bool:
        """
        Duplicate access token of winlogon.exe in active console session
        and impersonate it so the current thread can access Winsta0\\Winlogon.
        """
        if not IS_WINDOWS or advapi32 is None:
            return False

        try:
            session_id = 1
            if hasattr(kernel32, "WTSGetActiveConsoleSessionId"):
                sid = kernel32.WTSGetActiveConsoleSessionId()
                if sid != 0xFFFFFFFF:
                    session_id = sid

            TH32CS_SNAPPROCESS = 0x00000002
            h_snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if not h_snap or h_snap == wintypes.HANDLE(-1).value:
                return False

            winlogon_pid = None
            try:
                pe = PROCESSENTRY32W()
                pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                if kernel32.Process32FirstW(h_snap, ctypes.byref(pe)):
                    while True:
                        if pe.szExeFile.lower() == "winlogon.exe":
                            if hasattr(kernel32, "ProcessIdToSessionId"):
                                proc_sid = wintypes.DWORD()
                                if kernel32.ProcessIdToSessionId(pe.th32ProcessID, ctypes.byref(proc_sid)):
                                    if proc_sid.value == session_id:
                                        winlogon_pid = pe.th32ProcessID
                                        break
                            else:
                                winlogon_pid = pe.th32ProcessID
                                break
                        if not kernel32.Process32NextW(h_snap, ctypes.byref(pe)):
                            break
            finally:
                kernel32.CloseHandle(h_snap)

            if not winlogon_pid:
                return False

            PROCESS_QUERY_INFORMATION = 0x0400
            h_proc = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, winlogon_pid)
            if not h_proc:
                return False

            try:
                TOKEN_DUPLICATE = 0x0002
                TOKEN_QUERY = 0x0008
                TOKEN_IMPERSONATE = 0x0004
                h_token = wintypes.HANDLE()
                if not advapi32.OpenProcessToken(
                    h_proc,
                    TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_IMPERSONATE,
                    ctypes.byref(h_token),
                ):
                    return False

                try:
                    SecurityImpersonation = 2
                    TokenImpersonation = 2
                    h_dup_token = wintypes.HANDLE()
                    if not advapi32.DuplicateTokenEx(
                        h_token,
                        0x02000000,  # MAXIMUM_ALLOWED
                        None,
                        SecurityImpersonation,
                        TokenImpersonation,
                        ctypes.byref(h_dup_token),
                    ):
                        return False

                    try:
                        if advapi32.ImpersonateLoggedOnUser(h_dup_token):
                            logger.debug("Impersonating winlogon.exe (PID %d) for Secure Desktop access.", winlogon_pid)
                            return True
                        return False
                    finally:
                        kernel32.CloseHandle(h_dup_token)
                finally:
                    kernel32.CloseHandle(h_token)
            finally:
                kernel32.CloseHandle(h_proc)
        except Exception as exc:
            logger.debug("Failed impersonating winlogon: %s", exc)
            return False

    def ensure_input_desktop(self, force: bool = False) -> bool:
        """
        Dynamically attaches calling thread to active input desktop
        (Default user desktop, Winlogon/Lock/PIN screen, or UAC prompt).
        """
        if not IS_WINDOWS:
            return True

        tl = self._thread_local
        now = time.monotonic()
        last_check = getattr(tl, "last_check_ts", 0.0)
        if not force and (now - last_check < 0.25):
            return True
        tl.last_check_ts = now

        current_desk = getattr(tl, "current_desktop_handle", None)
        current_name = getattr(tl, "current_desktop_name", "")

        DESKTOP_ACCESS = 0x01FF  # All desktop specific rights
        h_desk = user32.OpenInputDesktop(0, False, DESKTOP_ACCESS)
        if not h_desk:
            err = kernel32.GetLastError()
            if err == 5:  # ERROR_ACCESS_DENIED (Desktop is Winlogon / Secure Desktop)
                if not self._privilege_enabled:
                    self._enable_se_debug_privilege()
                if self._impersonate_winlogon():
                    try:
                        h_desk = user32.OpenInputDesktop(0, False, DESKTOP_ACCESS)
                    finally:
                        advapi32.RevertToSelf()

        if not h_desk:
            if not self._access_denied_warned and kernel32.GetLastError() == 5:
                logger.warning(
                    "Access denied to active input desktop (Secure Desktop/PIN screen). "
                    "Ensure MultiControl runs with Administrator privileges or Windows Service Mode."
                )
                self._access_denied_warned = True
            return False

        self._access_denied_warned = False

        name_buf = (wintypes.WCHAR * 256)()
        needed = wintypes.DWORD()
        desk_name = ""
        if user32.GetUserObjectInformationW(
            h_desk, 2, ctypes.byref(name_buf), ctypes.sizeof(name_buf), ctypes.byref(needed)
        ):
            desk_name = str(name_buf.value).strip()

        if desk_name and desk_name.lower() == current_name.lower() and current_desk is not None:
            user32.CloseDesktop(h_desk)
            return True

        if user32.SetThreadDesktop(h_desk):
            logger.info("Attached thread to active desktop: '%s' (handle=0x%x)", desk_name, h_desk)
            if current_desk:
                try:
                    user32.CloseDesktop(current_desk)
                except Exception:
                    pass
            tl.current_desktop_handle = h_desk
            tl.current_desktop_name = desk_name
            return True
        else:
            err = kernel32.GetLastError()
            logger.debug("SetThreadDesktop('%s') failed: error %d", desk_name, err)
            user32.CloseDesktop(h_desk)
            return False

    def close(self) -> None:
        """Release held inputs and clean up desktop handles."""
        self.release_all()
        if IS_WINDOWS:
            tl = self._thread_local
            desk = getattr(tl, "current_desktop_handle", None)
            if desk:
                try:
                    user32.CloseDesktop(desk)
                except Exception:
                    pass
                tl.current_desktop_handle = None
                tl.current_desktop_name = ""

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
        ix, iy = int(x), int(y)
        self._last_x = ix
        self._last_y = iy
        if not IS_WINDOWS:
            return

        self.ensure_input_desktop(force=False)

        # Normalize to [0..65535] across the entire Windows Virtual Desktop
        vx, vy, vw, vh = self._virtual_screen_bounds()
        norm_x = int(round(((ix - vx) * 65535.0) / max(1, vw - 1)))
        norm_y = int(round(((iy - vy) * 65535.0) / max(1, vh - 1)))
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

        self.ensure_input_desktop(force=True)

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

        self.ensure_input_desktop(force=False)

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

        self.ensure_input_desktop(force=True)

        is_down = bool(state == 1)
        if not is_down and vk_code in self._held_keys:
            flags = self._held_keys.pop(vk_code)
        elif is_down:
            self._held_keys[vk_code] = flags

        scan_code = int(user32.MapVirtualKeyW(vk_code, MAPVK_VK_TO_VSC))
        dw_flags = 0
        if flags & 1:
            dw_flags |= KEYEVENTF_EXTENDEDKEY
        if not is_down:
            dw_flags |= KEYEVENTF_KEYUP

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

        for vk, flags in list(self._held_keys.items()):
            try:
                self.key_event(vk, 0, flags)
            except Exception:
                pass
        self._held_keys.clear()

    def leave_screen(self) -> None:
        """Called when cursor transitions from Windows back to Mac."""
        self.release_all()
        if IS_WINDOWS:
            # Park Windows cursor cleanly at right edge vertical center (away from taskbar & close buttons)
            vx, vy, vw, vh = self._virtual_screen_bounds()
            self.move_cursor(vx + vw - 1, vy + (vh // 2))
