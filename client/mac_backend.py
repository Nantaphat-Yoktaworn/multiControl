"""
macOS Native Backend using Quartz (CoreGraphics) & AppKit/pbcopy.

Responsibilities:
  1. Enumerate all active macOS displays with exact logical bounds & Retina scale factor.
  2. Capture global mouse movement, mouse buttons, smooth trackpad/wheel scroll,
     and keyboard events via CGEventTapCreate.
  3. Lock/disassociate and hide the local Mac cursor when the virtual cursor
     enters the Windows screen, swallowing local Mac input so clicks/keys only
     go to Windows.
  4. Warp the Mac cursor to the exact entry coordinate and restore local control
     seamlessly when the cursor crosses back from Windows to Mac.
  5. Read/write macOS clipboard via pbcopy/pbpaste.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Callable, List, Optional, Set, Tuple

from layout_engine import DisplayRect

logger = logging.getLogger("multicontrol.mac")

try:
    import Quartz
    HAS_QUARTZ = True
except ImportError:
    Quartz = None  # type: ignore
    HAS_QUARTZ = False


# Device-dependent modifier masks in CGEventFlags (IOKit/IOLLEvent.h)
NX_DEVICELCTLKEYMASK = 0x00000001
NX_DEVICELSHIFTKEYMASK = 0x00000002
NX_DEVICERSHIFTKEYMASK = 0x00000004
NX_DEVICELCMDKEYMASK = 0x00000008
NX_DEVICERCMDKEYMASK = 0x00000010
NX_DEVICELALTKEYMASK = 0x00000020
NX_DEVICERALTKEYMASK = 0x00000040
NX_DEVICERCTLKEYMASK = 0x00002000

MODIFIER_KEYCODE_MASKS = {
    0x37: (NX_DEVICELCMDKEYMASK, 0x100000),    # Left Command (kCGEventFlagMaskCommand)
    0x36: (NX_DEVICERCMDKEYMASK, 0x100000),    # Right Command
    0x38: (NX_DEVICELSHIFTKEYMASK, 0x020000),  # Left Shift (kCGEventFlagMaskShift)
    0x3C: (NX_DEVICERSHIFTKEYMASK, 0x020000),  # Right Shift
    0x3A: (NX_DEVICELALTKEYMASK, 0x080000),    # Left Option (kCGEventFlagMaskAlternate)
    0x3D: (NX_DEVICERALTKEYMASK, 0x080000),    # Right Option
    0x3B: (NX_DEVICELCTLKEYMASK, 0x040000),    # Left Control (kCGEventFlagMaskControl)
    0x3E: (NX_DEVICERCTLKEYMASK, 0x040000),    # Right Control
    0x39: (0x010000, 0x010000),                # Caps Lock (kCGEventFlagMaskAlphaShift)
}


def get_mac_displays(device_id: str, device_name: str) -> List[DisplayRect]:
    """Enumerate all active macOS displays using Quartz CoreGraphics."""
    if not HAS_QUARTZ:
        return [
            DisplayRect(
                id=f"{device_id}:0",
                device_id=device_id,
                device_name=device_name,
                os_type="darwin",
                monitor_index=0,
                name="Mac Display",
                primary=True,
                os_x=0.0,
                os_y=0.0,
                os_width=1512.0,
                os_height=982.0,
                scale_factor=2.0,
                canvas_x=0.0,
                canvas_y=0.0,
                canvas_width=1512.0,
                canvas_height=982.0,
            )
        ]

    err, active_ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
    if err != 0 or not active_ids:
        main_id = Quartz.CGMainDisplayID()
        active_ids = [main_id]
        count = 1

    main_id = Quartz.CGMainDisplayID()
    displays: List[DisplayRect] = []

    for idx, d_id in enumerate(active_ids[:count]):
        bounds = Quartz.CGDisplayBounds(d_id)
        ox = float(bounds.origin.x)
        oy = float(bounds.origin.y)
        ow = float(bounds.size.width)
        oh = float(bounds.size.height)

        scale = 1.0
        try:
            mode = Quartz.CGDisplayCopyDisplayMode(d_id)
            if mode is not None:
                pw = float(Quartz.CGDisplayModeGetPixelWidth(mode))
                if ow > 0 and pw > 0:
                    scale = round(pw / ow, 2)
        except Exception:
            pass

        is_primary = (d_id == main_id)
        label = (
            "Mac Built-in / Main Display"
            if is_primary
            else f"Mac External Display {idx + 1}"
        )
        displays.append(
            DisplayRect(
                id=f"{device_id}:{idx}",
                device_id=device_id,
                device_name=device_name,
                os_type="darwin",
                monitor_index=idx,
                name=label,
                primary=is_primary,
                os_x=ox,
                os_y=oy,
                os_width=ow,
                os_height=oh,
                scale_factor=scale,
                canvas_x=ox,
                canvas_y=oy,
                canvas_width=ow,
                canvas_height=oh,
            )
        )

    return displays


def get_mac_clipboard() -> str:
    """Read current plain-text clipboard on macOS."""
    try:
        res = subprocess.run(
            ["pbpaste"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout
    except Exception:
        pass
    return ""


def set_mac_clipboard(text: str) -> None:
    """Write plain-text clipboard on macOS."""
    try:
        subprocess.run(
            ["pbcopy"],
            input=text,
            text=True,
            timeout=1.0,
            check=False,
        )
    except Exception:
        pass


def open_accessibility_settings() -> None:
    """
    Trigger native macOS Accessibility & Input Monitoring permission prompt
    (which adds Python directly to the System Settings list) and open Accessibility settings.
    """
    if HAS_QUARTZ:
        try:
            if hasattr(Quartz, "CGRequestPostEventAccess"):
                Quartz.CGRequestPostEventAccess()
            if hasattr(Quartz, "CGRequestListenEventAccess"):
                Quartz.CGRequestListenEventAccess()
        except Exception:
            pass
    try:
        subprocess.Popen(
            [
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            ]
        )
    except Exception:
        pass


class MacInputController:
    """
    Captures global macOS mouse and keyboard events via a Quartz CGEventTap
    and manages locking/unlocking the local Mac cursor during remote control.
    """

    def __init__(
        self,
        on_mouse_move: Callable[[float, float, float, float, bool], None],
        on_mouse_button: Callable[[int, int], None],
        on_mouse_scroll: Callable[[float, float, bool], None],
        on_key_event: Callable[[int, int], None],
        on_emergency_return: Callable[[], None],
        on_toggle_screen_lock: Callable[[], None],
    ) -> None:
        self.on_mouse_move = on_mouse_move
        self.on_mouse_button = on_mouse_button
        self.on_mouse_scroll = on_mouse_scroll
        self.on_key_event = on_key_event
        self.on_emergency_return = on_emergency_return
        self.on_toggle_screen_lock = on_toggle_screen_lock

        self.remote_active: bool = False
        self.screen_locked: bool = False
        self._cursor_hidden: bool = False
        self._park_pos: Tuple[float, float] = (500.0, 500.0)
        self._pressed_modifiers: Set[int] = set()
        self._local_modifiers_down: Set[int] = set()
        self._local_buttons_down: Set[int] = set()
        self._tap = None
        self._run_loop = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._ignore_next_warp_until: float = 0.0
        self._cgs_lib = None
        self._cgs_cid: int = 0
        self._init_cgs_cursor_api()

    def _init_cgs_cursor_api(self) -> None:
        """
        Enable 'SetsCursorInBackground' via CoreGraphics private CGS API
        (used by Synergy / Barrier / Input Leap) so CGSHideCursor / CGDisplayHideCursor
        hides the macOS hardware cursor globally even when running in the background.
        """
        if not HAS_QUARTZ:
            return
        try:
            import ctypes
            cf = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )
            cg = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            )
            cf.CFStringCreateWithCString.argtypes = [
                ctypes.c_void_p,
                ctypes.c_char_p,
                ctypes.c_uint32,
            ]
            cf.CFStringCreateWithCString.restype = ctypes.c_void_p
            kCFBooleanTrue = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")

            cg._CGSDefaultConnection.restype = ctypes.c_uint32
            cg.CGSSetConnectionProperty.argtypes = [
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            cg.CGSSetConnectionProperty.restype = ctypes.c_int32
            cg.CGSHideCursor.argtypes = [ctypes.c_uint32]
            cg.CGSHideCursor.restype = ctypes.c_int32
            cg.CGSShowCursor.argtypes = [ctypes.c_uint32]
            cg.CGSShowCursor.restype = ctypes.c_int32

            cid = cg._CGSDefaultConnection()
            key = cf.CFStringCreateWithCString(
                None, b"SetsCursorInBackground", 0x08000100
            )
            cg.CGSSetConnectionProperty(cid, cid, key, kCFBooleanTrue)
            self._cgs_lib = cg
            self._cgs_cid = cid

            try:
                src = Quartz.CGEventSourceCreate(
                    Quartz.kCGEventSourceStateCombinedSessionState
                )
                if src is not None:
                    Quartz.CGEventSourceSetLocalEventsSuppressionInterval(src, 0.0)
            except Exception:
                pass
        except Exception as exc:
            logger.debug("CGS cursor API init warning: %s", exc)

    def _hide_cursor_now(self) -> None:
        if not HAS_QUARTZ:
            return
        try:
            if self._cgs_lib is not None and self._cgs_cid:
                self._cgs_lib.CGSHideCursor(self._cgs_cid)
            Quartz.CGDisplayHideCursor(Quartz.CGMainDisplayID())
            self._cursor_hidden = True
        except Exception:
            pass

    def _show_cursor_now(self) -> None:
        if not HAS_QUARTZ:
            return
        try:
            for _ in range(6):
                if self._cgs_lib is not None and self._cgs_cid:
                    self._cgs_lib.CGSShowCursor(self._cgs_cid)
                Quartz.CGDisplayShowCursor(Quartz.CGMainDisplayID())
                if Quartz.CGCursorIsVisible():
                    break
            self._cursor_hidden = False
        except Exception:
            pass

    def set_park_position(self, x: float, y: float) -> None:
        """Set safe center point on primary Mac screen where cursor parks when remote is active."""
        self._park_pos = (x, y)

    def enter_remote_mode(self) -> None:
        """Lock and hide local Mac cursor so mouse/keyboard control Windows exclusively."""
        if not HAS_QUARTZ or self.remote_active:
            self.remote_active = True
            return

        self.remote_active = True
        try:
            Quartz.CGAssociateMouseAndMouseCursorPosition(False)
            self._hide_cursor_now()
        except Exception as exc:
            logger.warning("Failed to lock Mac cursor: %s", exc)

    def leave_remote_mode(self, entry_os_x: float, entry_os_y: float) -> None:
        """Restore local Mac cursor at exact entry point `(entry_os_x, entry_os_y)`."""
        self.remote_active = False
        self._pressed_modifiers.clear()
        if not HAS_QUARTZ:
            return

        try:
            self._ignore_next_warp_until = time.monotonic() + 0.02
            Quartz.CGWarpMouseCursorPosition((float(entry_os_x), float(entry_os_y)))
            Quartz.CGAssociateMouseAndMouseCursorPosition(True)
            self._show_cursor_now()
        except Exception as exc:
            logger.warning("Failed to restore Mac cursor: %s", exc)

    def get_current_cursor_pos(self) -> Tuple[float, float]:
        """Return current macOS cursor coordinates."""
        if not HAS_QUARTZ:
            return (0.0, 0.0)
        try:
            ev = Quartz.CGEventCreate(None)
            loc = Quartz.CGEventGetLocation(ev)
            return float(loc.x), float(loc.y)
        except Exception:
            return (0.0, 0.0)

    def start(self) -> bool:
        """Start the Quartz CGEventTap runloop in a dedicated high-priority thread."""
        if not HAS_QUARTZ:
            logger.error("pyobjc-framework-Quartz is not installed on this Mac.")
            return False

        if self._running:
            return True

        ready_event = threading.Event()
        tap_ok = [False]

        def _loop() -> None:
            mask = (
                Quartz.CGEventMaskBit(Quartz.kCGEventMouseMoved)
                | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDragged)
                | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDragged)
                | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseDragged)
                | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDown)
                | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseUp)
                | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDown)
                | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseUp)
                | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseDown)
                | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseUp)
                | Quartz.CGEventMaskBit(Quartz.kCGEventScrollWheel)
                | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
                | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
                | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
            )

            prompted_settings = False
            while self._tap is None:
                self._tap = Quartz.CGEventTapCreate(
                    Quartz.kCGHIDEventTap,
                    Quartz.kCGHeadInsertEventTap,
                    Quartz.kCGEventTapOptionDefault,
                    mask,
                    self._event_tap_callback,
                    None,
                )
                if self._tap is not None:
                    break
                if not prompted_settings:
                    prompted_settings = True
                    logger.warning(
                        "Waiting for macOS Accessibility permission! Please grant permission "
                        "to Python in System Settings -> Privacy & Security -> Accessibility. "
                        "(Will auto-activate as soon as you toggle it on!)"
                    )
                    open_accessibility_settings()
                    ready_event.set()
                time.sleep(2.0)

            tap_ok[0] = True
            self._running = True
            logger.info("macOS CGEventTap successfully attached and active!")
            source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
            self._run_loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(
                self._run_loop, source, Quartz.kCFRunLoopCommonModes
            )
            Quartz.CGEventTapEnable(self._tap, True)
            ready_event.set()
            Quartz.CFRunLoopRun()

        self._thread = threading.Thread(target=_loop, name="MacEventTap", daemon=True)
        self._thread.start()
        ready_event.wait(timeout=3.0)
        return tap_ok[0]

    def stop(self) -> None:
        self._running = False
        if self.remote_active:
            self.leave_remote_mode(*self._park_pos)
        if HAS_QUARTZ and self._run_loop is not None:
            try:
                Quartz.CFRunLoopStop(self._run_loop)
            except Exception:
                pass

    def _event_tap_callback(self, proxy, event_type, event, refcon):
        try:
            # Re-enable tap if macOS temporarily disabled it
            if event_type in (
                Quartz.kCGEventTapDisabledByTimeout,
                Quartz.kCGEventTapDisabledByUserInput,
            ):
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                return event

            # 1. Mouse Movement & Dragging
            if event_type in (
                Quartz.kCGEventMouseMoved,
                Quartz.kCGEventLeftMouseDragged,
                Quartz.kCGEventRightMouseDragged,
                Quartz.kCGEventOtherMouseDragged,
            ):
                if time.monotonic() < self._ignore_next_warp_until:
                    return None if self.remote_active else event

                dx = float(
                    Quartz.CGEventGetDoubleValueField(event, Quartz.kCGMouseEventDeltaX)
                )
                dy = float(
                    Quartz.CGEventGetDoubleValueField(event, Quartz.kCGMouseEventDeltaY)
                )
                loc = Quartz.CGEventGetLocation(event)
                ox, oy = float(loc.x), float(loc.y)
                is_dragging = (
                    event_type != Quartz.kCGEventMouseMoved
                    or bool(self._local_buttons_down)
                )

                self.on_mouse_move(ox, oy, dx, dy, is_dragging)
                if self.remote_active:
                    Quartz.CGAssociateMouseAndMouseCursorPosition(False)
                    if Quartz.CGCursorIsVisible():
                        self._hide_cursor_now()
                    return None
                return event

            # 2. Check Emergency Return Hotkey on KeyDown before swallowing
            if event_type == Quartz.kCGEventKeyDown:
                keycode = int(
                    Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode
                    )
                )
                flags = int(Quartz.CGEventGetFlags(event))
                ctrl_held = bool(flags & Quartz.kCGEventFlagMaskControl)
                opt_held = bool(flags & Quartz.kCGEventFlagMaskAlternate)

                # Emergency Return Hotkey: Ctrl+Opt+Esc (Esc = 0x35)
                if keycode == 0x35 and ctrl_held and opt_held:
                    self.on_emergency_return()
                    return None

            # Track local mouse buttons & modifiers when cursor is on Mac
            if not self.remote_active:
                if event_type == Quartz.kCGEventLeftMouseDown:
                    self._local_buttons_down.add(1)
                elif event_type == Quartz.kCGEventLeftMouseUp:
                    self._local_buttons_down.discard(1)
                elif event_type == Quartz.kCGEventRightMouseDown:
                    self._local_buttons_down.add(2)
                elif event_type == Quartz.kCGEventRightMouseUp:
                    self._local_buttons_down.discard(2)
                elif event_type == Quartz.kCGEventOtherMouseDown:
                    self._local_buttons_down.add(3)
                elif event_type == Quartz.kCGEventOtherMouseUp:
                    self._local_buttons_down.discard(3)
                elif event_type == Quartz.kCGEventFlagsChanged:
                    keycode = int(
                        Quartz.CGEventGetIntegerValueField(
                            event, Quartz.kCGKeyboardEventKeycode
                        )
                    )
                    flags = int(Quartz.CGEventGetFlags(event))
                    if keycode in MODIFIER_KEYCODE_MASKS:
                        dev_mask, std_mask = MODIFIER_KEYCODE_MASKS[keycode]
                        if (flags & dev_mask) or (flags & std_mask):
                            self._local_modifiers_down.add(keycode)
                        else:
                            self._local_modifiers_down.discard(keycode)
                return event

            # 3. Remote is Active: intercept buttons, scroll, and keys and send to Windows!
            if event_type == Quartz.kCGEventLeftMouseDown:
                self.on_mouse_button(1, 1)
                return None
            if event_type == Quartz.kCGEventLeftMouseUp:
                self.on_mouse_button(1, 0)
                return None
            if event_type == Quartz.kCGEventRightMouseDown:
                self.on_mouse_button(2, 1)
                return None
            if event_type == Quartz.kCGEventRightMouseUp:
                self.on_mouse_button(2, 0)
                return None
            if event_type == Quartz.kCGEventOtherMouseDown:
                btn_num = int(
                    Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGMouseEventButtonNumber
                    )
                )
                mapped_btn = 3 if btn_num == 2 else min(5, btn_num + 1)
                self.on_mouse_button(mapped_btn, 1)
                return None
            if event_type == Quartz.kCGEventOtherMouseUp:
                btn_num = int(
                    Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGMouseEventButtonNumber
                    )
                )
                mapped_btn = 3 if btn_num == 2 else min(5, btn_num + 1)
                self.on_mouse_button(mapped_btn, 0)
                return None

            if event_type == Quartz.kCGEventScrollWheel:
                is_continuous = bool(
                    Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGScrollWheelEventIsContinuous
                    )
                )
                if is_continuous:
                    sy = float(
                        Quartz.CGEventGetDoubleValueField(
                            event, Quartz.kCGScrollWheelEventPointDeltaAxis1
                        )
                    )
                    sx = float(
                        Quartz.CGEventGetDoubleValueField(
                            event, Quartz.kCGScrollWheelEventPointDeltaAxis2
                        )
                    )
                else:
                    sy = float(
                        Quartz.CGEventGetDoubleValueField(
                            event, Quartz.kCGScrollWheelEventFixedPtDeltaAxis1
                        )
                    )
                    sx = float(
                        Quartz.CGEventGetDoubleValueField(
                            event, Quartz.kCGScrollWheelEventFixedPtDeltaAxis2
                        )
                    )
                if sx != 0.0 or sy != 0.0:
                    self.on_mouse_scroll(sx, sy, is_continuous)
                return None

            if event_type == Quartz.kCGEventKeyDown:
                keycode = int(
                    Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode
                    )
                )
                self.on_key_event(keycode, 1)
                return None

            if event_type == Quartz.kCGEventKeyUp:
                keycode = int(
                    Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode
                    )
                )
                self.on_key_event(keycode, 0)
                return None

            if event_type == Quartz.kCGEventFlagsChanged:
                keycode = int(
                    Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode
                    )
                )
                flags = int(Quartz.CGEventGetFlags(event))
                if keycode in MODIFIER_KEYCODE_MASKS:
                    dev_mask, std_mask = MODIFIER_KEYCODE_MASKS[keycode]
                    if flags & dev_mask:
                        is_down = True
                    elif not (flags & std_mask):
                        is_down = False
                    else:
                        is_down = keycode not in self._pressed_modifiers

                    # If modifier was pressed on Mac before entering remote mode, let Mac receive its release
                    if not is_down and keycode in self._local_modifiers_down:
                        self._local_modifiers_down.discard(keycode)
                        return event

                    if is_down:
                        self._pressed_modifiers.add(keycode)
                        self.on_key_event(keycode, 1)
                    else:
                        self._pressed_modifiers.discard(keycode)
                        self.on_key_event(keycode, 0)
                return None

        except Exception as exc:
            logger.debug("Event tap callback exception: %s", exc)

        return event
