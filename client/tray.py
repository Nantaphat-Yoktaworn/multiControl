"""
Cross-platform Menu Bar (macOS) & System Tray (Windows) icon for MultiControl.
Uses pystray + Pillow when available, with graceful fallback to console mode.
"""

from __future__ import annotations

import logging
import sys
import webbrowser
from typing import Callable, Optional

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


def _create_tray_image(state: str = "connected"):
    """Draw a crisp 64x64 dual-monitor icon with status badge."""
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

    # Status dot
    colors = {
        "disconnected": (245, 158, 11, 255),  # Amber
        "connected": (34, 197, 94, 255),      # Emerald green
        "remote": (56, 189, 248, 255),        # Cyan (actively controlling remote)
        "locked": (168, 85, 247, 255),        # Purple (screen locked)
    }
    badge_color = colors.get(state, colors["connected"])
    draw.ellipse((44, 6, 58, 20), fill=badge_color, outline=(15, 23, 42, 255), width=2)
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
        self._icon: Optional[object] = None

    def update_status(
        self,
        status_text: str,
        transport_text: Optional[str] = None,
        state: str = "connected",
        screen_locked: Optional[bool] = None,
    ) -> None:
        self.status_text = status_text
        if transport_text is not None:
            self.transport_text = transport_text
        if screen_locked is not None:
            self.screen_locked = screen_locked
        self._state = "locked" if self.screen_locked else state

        # On macOS, AppKit NSStatusItem/NSMenu must NEVER be mutated from background
        # threads (doing so raises NSInternalInconsistencyException / SIGTRAP).
        # On Windows, win32 Shell_NotifyIcon is thread-safe.
        if HAS_TRAY and self._icon is not None and sys.platform == "win32":
            try:
                self._icon.icon = _create_tray_image(self._state)
                self._icon.title = f"MultiControl — {self.status_text}"
                self._icon.update_menu()
            except Exception:
                pass

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
            self._icon = pystray.Icon(
                "MultiControl",
                _create_tray_image("connected"),
                "MultiControl",
                menu=self._build_menu(),
            )
            self._icon.run()
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
