"""
Cross-platform Menu Bar (macOS) & System Tray (Windows) icon for MultiControl.
Uses pystray + Pillow when available, with graceful fallback to console mode.
"""

from __future__ import annotations

import logging
import sys
import threading
import webbrowser
from typing import Callable, Dict, Optional

logger = logging.getLogger("multicontrol.tray")

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    pystray = None  # type: ignore
    Image = None    # type: ignore
    ImageDraw = None  # type: ignore
    HAS_TRAY = False

_ICON_CACHE: Dict[str, object] = {}


def _create_tray_image(state: str = "connected"):
    """Return cached 64x64 dual-monitor icon with status badge."""
    if state in _ICON_CACHE:
        return _ICON_CACHE[state]

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Left monitor (Mac)
    draw.rounded_rectangle(
        (4, 12, 34, 36),
        radius=4,
        fill=(30, 41, 59, 240),
        outline=(148, 163, 184, 255),
        width=3,
    )
    # Right monitor (Windows)
    draw.rounded_rectangle(
        (28, 22, 60, 48),
        radius=4,
        fill=(15, 23, 42, 245),
        outline=(56, 189, 248, 255),
        width=3,
    )

    colors = {
        "disconnected": (245, 158, 11, 255),  # Amber
        "connected": (34, 197, 94, 255),      # Emerald green
        "remote": (56, 189, 248, 255),        # Cyan (actively controlling remote)
        "locked": (168, 85, 247, 255),        # Purple (screen locked)
    }
    badge_color = colors.get(state, colors["connected"])
    draw.ellipse((44, 6, 58, 20), fill=badge_color, outline=(15, 23, 42, 255), width=2)
    _ICON_CACHE[state] = img
    return img


class TrayController:
    """Manages the macOS Menu Bar or Windows System Tray icon."""

    def __init__(
        self,
        web_ui_url: str,
        is_mac: bool,
        on_return_to_mac: Callable[[], None],
        on_toggle_lock: Callable[[], None],
        on_sync_clipboard: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self.web_ui_url = web_ui_url
        self.is_mac = is_mac
        self.on_return_to_mac = on_return_to_mac
        self.on_toggle_lock = on_toggle_lock
        self.on_sync_clipboard = on_sync_clipboard
        self.on_quit = on_quit

        self.status_text = "Connecting to Server..."
        self.transport_text = "Mode: Handshaking"
        self.screen_locked = False
        self._state = "disconnected"
        self._last_applied_state = ""
        self._icon: Optional[object] = None

    def _sync_darwin_ui(self) -> None:
        """Execute all macOS AppKit NSStatusItem / NSMenu mutations strictly on the main thread."""
        if self._icon is None:
            return
        try:
            menu_handle = getattr(self._icon, "_menu_handle", None)
            if menu_handle:
                nsmenu = menu_handle[0]
                if nsmenu is not None and nsmenu.numberOfItems() >= 2:
                    nsmenu.itemAtIndex_(0).setTitle_(f"● {self.status_text}")
                    nsmenu.itemAtIndex_(1).setTitle_(f"   {self.transport_text}")
            else:
                self._icon.update_menu()

            if self._last_applied_state != self._state:
                self._last_applied_state = self._state
                self._icon.icon = _create_tray_image(self._state)

            self._icon.title = f"MultiControl — {self.status_text}"
        except Exception as exc:
            logger.debug("Darwin tray sync error: %s", exc)

    def _sync_win_ui(self, state_changed: bool, text_changed: bool) -> None:
        """Update Win32 system tray icon and menu off the UDP hot path."""
        if self._icon is None:
            return
        try:
            if state_changed and self._last_applied_state != self._state:
                self._last_applied_state = self._state
                self._icon.icon = _create_tray_image(self._state)
            if state_changed or text_changed:
                self._icon.title = f"MultiControl — {self.status_text}"
                self._icon.update_menu()
        except Exception as exc:
            logger.debug("Win32 tray sync error: %s", exc)

    def update_status(
        self,
        status_text: str,
        transport_text: Optional[str] = None,
        state: Optional[str] = None,
        screen_locked: Optional[bool] = None,
    ) -> None:
        new_locked = self.screen_locked if screen_locked is None else screen_locked
        base_state = (self._state if self._state != "disconnected" else "connected") if state is None else state
        new_state = "locked" if new_locked else base_state
        new_transport = self.transport_text if transport_text is None else transport_text

        state_changed = new_state != self._state
        text_changed = (status_text != self.status_text) or (new_transport != self.transport_text)

        self.status_text = status_text
        self.transport_text = new_transport
        self.screen_locked = new_locked
        self._state = new_state

        if not (state_changed or text_changed):
            return

        if not HAS_TRAY or self._icon is None:
            return

        if sys.platform == "darwin":
            try:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(self._sync_darwin_ui)
            except Exception as exc:
                logger.debug("Failed to schedule Darwin tray update: %s", exc)
        elif sys.platform == "win32":
            threading.Thread(
                target=self._sync_win_ui,
                args=(state_changed, text_changed),
                daemon=True,
            ).start()

    def _open_webui(self, icon=None, item=None) -> None:
        webbrowser.open(self.web_ui_url)

    def _return_mac(self, icon=None, item=None) -> None:
        self.on_return_to_mac()

    def _sync_clip(self, icon=None, item=None) -> None:
        self.on_sync_clipboard()

    def _quit(self, icon=None, item=None) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
        self.on_quit()

    def _build_menu(self):
        items = [
            pystray.MenuItem(lambda item: f"● {self.status_text}", None, enabled=False),
            pystray.MenuItem(lambda item: f"   {self.transport_text}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Screen Layout Studio (Web UI)", self._open_webui, default=True),
            pystray.MenuItem("Sync Clipboard Now", self._sync_clip),
        ]
        if self.is_mac:
            items.extend(
                [
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem(
                        "Return Cursor to Mac (Ctrl+Opt+Esc)",
                        self._return_mac,
                    ),
                ]
            )
        items.extend(
            [
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit MultiControl", self._quit),
            ]
        )
        return pystray.Menu(*items)

    def run_blocking(self) -> bool:
        """
        Run the system tray icon on the main thread (required by macOS AppKit).
        Returns False if pystray is unavailable.
        """
        if not HAS_TRAY:
            return False

        try:
            for st in ("disconnected", "connected", "remote", "locked"):
                _create_tray_image(st)
            self._icon = pystray.Icon(
                "MultiControl",
                _create_tray_image(self._state if self._state in _ICON_CACHE else "connected"),
                f"MultiControl — {self.status_text}",
                menu=self._build_menu(),
            )

            def _setup(icon) -> None:
                if sys.platform == "darwin":
                    try:
                        from PyObjCTools import AppHelper

                        def _init_on_main() -> None:
                            icon.visible = True
                            self._sync_darwin_ui()

                        AppHelper.callAfter(_init_on_main)
                        return
                    except Exception:
                        pass
                icon.visible = True
                if sys.platform == "win32":
                    self._sync_win_ui(True, True)

            self._icon.run(setup=_setup)
            return True
        except Exception as exc:
            logger.warning("System tray unavailable (%s), continuing in console mode.", exc)
            return False

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
