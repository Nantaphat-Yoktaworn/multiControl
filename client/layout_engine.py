"""
2D Virtual Canvas & Seamless Monitor Edge-Transition Engine.

Maps between each operating system's native display coordinate space
and the shared 2D Virtual Canvas where displays can be freely positioned
and scaled to match physical monitor dimensions on the user's desk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class DisplayRect:
    """Represents a single physical/logical display on the 2D Virtual Canvas."""

    id: str                  # Unique ID, e.g. "mac-1:0" or "win-pc:0"
    device_id: str           # Owner device_id
    device_name: str         # Human-readable device name
    os_type: str             # "darwin" | "windows" | "linux"
    monitor_index: int       # 0-based index on that machine
    name: str                # e.g. "Built-in Retina Display" or "DELL U2723QE"
    primary: bool = False

    # Native OS coordinates (used by Quartz CGWarpMouseCursorPosition / Win32 SetCursorPos)
    os_x: float = 0.0
    os_y: float = 0.0
    os_width: float = 1920.0
    os_height: float = 1080.0
    scale_factor: float = 1.0

    # Virtual Canvas coordinates (editable in Web UI via drag-and-drop & resize)
    canvas_x: float = 0.0
    canvas_y: float = 0.0
    canvas_width: float = 1920.0
    canvas_height: float = 1080.0

    @property
    def canvas_right(self) -> float:
        return self.canvas_x + self.canvas_width

    @property
    def canvas_bottom(self) -> float:
        return self.canvas_y + self.canvas_height

    @property
    def os_right(self) -> float:
        return self.os_x + self.os_width

    @property
    def os_bottom(self) -> float:
        return self.os_y + self.os_height

    def contains_canvas(self, cx: float, cy: float, eps: float = 0.0) -> bool:
        return (
            (self.canvas_x - eps) <= cx <= (self.canvas_right + eps)
            and (self.canvas_y - eps) <= cy <= (self.canvas_bottom + eps)
        )

    def contains_os(self, ox: float, oy: float, eps: float = 1.0) -> bool:
        return (
            (self.os_x - eps) <= ox <= (self.os_right + eps)
            and (self.os_y - eps) <= oy <= (self.os_bottom + eps)
        )

    def os_to_canvas(self, ox: float, oy: float) -> Tuple[float, float]:
        """Convert a point in native OS display coords to Virtual Canvas coords."""
        w = max(1.0, self.os_width)
        h = max(1.0, self.os_height)
        u = (ox - self.os_x) / w
        v = (oy - self.os_y) / h
        u = max(0.0, min(1.0, u))
        v = max(0.0, min(1.0, v))
        return (
            self.canvas_x + u * self.canvas_width,
            self.canvas_y + v * self.canvas_height,
        )

    def canvas_to_os(self, cx: float, cy: float) -> Tuple[int, int]:
        """Convert a point in Virtual Canvas coords to native OS pixel coords."""
        cw = max(1.0, self.canvas_width)
        ch = max(1.0, self.canvas_height)
        u = (cx - self.canvas_x) / cw
        v = (cy - self.canvas_y) / ch
        u = max(0.0, min(1.0, u))
        v = max(0.0, min(1.0, v))
        ox = int(round(self.os_x + u * max(1.0, self.os_width - 1.0)))
        oy = int(round(self.os_y + v * max(1.0, self.os_height - 1.0)))
        return ox, oy

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "os_type": self.os_type,
            "monitor_index": self.monitor_index,
            "name": self.name,
            "primary": self.primary,
            "os_x": self.os_x,
            "os_y": self.os_y,
            "os_width": self.os_width,
            "os_height": self.os_height,
            "scale_factor": self.scale_factor,
            "canvas_x": self.canvas_x,
            "canvas_y": self.canvas_y,
            "canvas_width": self.canvas_width,
            "canvas_height": self.canvas_height,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DisplayRect":
        return cls(
            id=str(data["id"]),
            device_id=str(data["device_id"]),
            device_name=str(data.get("device_name", data["device_id"])),
            os_type=str(data.get("os_type", "unknown")),
            monitor_index=int(data.get("monitor_index", 0)),
            name=str(data.get("name", f"Display {data.get('monitor_index', 0) + 1}")),
            primary=bool(data.get("primary", False)),
            os_x=float(data.get("os_x", 0.0)),
            os_y=float(data.get("os_y", 0.0)),
            os_width=float(data.get("os_width", 1920.0)),
            os_height=float(data.get("os_height", 1080.0)),
            scale_factor=float(data.get("scale_factor", 1.0)),
            canvas_x=float(data.get("canvas_x", data.get("os_x", 0.0))),
            canvas_y=float(data.get("canvas_y", data.get("os_y", 0.0))),
            canvas_width=float(data.get("canvas_width", data.get("os_width", 1920.0))),
            canvas_height=float(data.get("canvas_height", data.get("os_height", 1080.0))),
        )


@dataclass
class LayoutSettings:
    """Global layout & input behavior settings."""

    swap_cmd_ctrl: bool = True
    swap_opt_win: bool = False
    clipboard_sync: bool = True
    invert_scroll_on_windows: bool = False
    scroll_speed: float = 1.0
    mouse_speed_windows: float = 1.0
    corner_barrier_px: float = 0.0    # Full edge active by default (no corner barrier)
    edge_snap_tolerance_px: float = 18.0  # Gap tolerance between adjacent monitors on canvas
    wrap_around: bool = False

    def to_dict(self) -> dict:
        return {
            "swap_cmd_ctrl": self.swap_cmd_ctrl,
            "swap_opt_win": self.swap_opt_win,
            "clipboard_sync": self.clipboard_sync,
            "invert_scroll_on_windows": self.invert_scroll_on_windows,
            "scroll_speed": self.scroll_speed,
            "mouse_speed_windows": self.mouse_speed_windows,
            "corner_barrier_px": self.corner_barrier_px,
            "edge_snap_tolerance_px": self.edge_snap_tolerance_px,
            "wrap_around": self.wrap_around,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LayoutSettings":
        if not data:
            return cls()
        return cls(
            swap_cmd_ctrl=bool(data.get("swap_cmd_ctrl", True)),
            swap_opt_win=bool(data.get("swap_opt_win", False)),
            clipboard_sync=bool(data.get("clipboard_sync", True)),
            invert_scroll_on_windows=bool(data.get("invert_scroll_on_windows", False)),
            scroll_speed=float(data.get("scroll_speed", 1.0)),
            mouse_speed_windows=float(data.get("mouse_speed_windows", 1.0)),
            corner_barrier_px=float(data.get("corner_barrier_px", 0.0)),
            edge_snap_tolerance_px=float(data.get("edge_snap_tolerance_px", 18.0)),
            wrap_around=bool(data.get("wrap_around", False)),
        )


@dataclass
class TransitionResult:
    """Result of advancing the cursor on the 2D Virtual Canvas."""

    display: DisplayRect
    canvas_x: float
    canvas_y: float
    os_x: int
    os_y: int
    device_changed: bool = False
    display_changed: bool = False


class VirtualCanvasEngine:
    """
    Computes cursor positioning and seamless edge transitions across displays
    from multiple machines on the 2D Virtual Canvas.
    """

    def __init__(
        self,
        displays: Optional[List[DisplayRect]] = None,
        settings: Optional[LayoutSettings] = None,
    ) -> None:
        self.displays: Dict[str, DisplayRect] = {}
        self.settings: LayoutSettings = settings or LayoutSettings()
        if displays:
            for d in displays:
                self.displays[d.id] = d

    def update_from_dict(self, state: dict) -> None:
        if "settings" in state:
            self.settings = LayoutSettings.from_dict(state["settings"])
        if "displays" in state:
            new_map: Dict[str, DisplayRect] = {}
            for item in state["displays"]:
                d = DisplayRect.from_dict(item)
                new_map[d.id] = d
            self.displays = new_map

    def find_display_by_os_point(
        self, device_id: str, ox: float, oy: float
    ) -> Optional[DisplayRect]:
        """Find which display of `device_id` contains native OS point `(ox, oy)`."""
        device_displays = [d for d in self.displays.values() if d.device_id == device_id]
        if not device_displays:
            return None
        for d in device_displays:
            if d.contains_os(ox, oy, eps=2.0):
                return d
        # Fallback: find nearest display on that device
        def dist_sq(d: DisplayRect) -> float:
            cx = max(d.os_x, min(ox, d.os_right))
            cy = max(d.os_y, min(oy, d.os_bottom))
            return (ox - cx) ** 2 + (oy - cy) ** 2

        return min(device_displays, key=dist_sq)

    def _in_corner_barrier(self, d: DisplayRect, cx: float, cy: float) -> bool:
        """Check if `(cx, cy)` is within the corner barrier of display `d`."""
        b = self.settings.corner_barrier_px
        if b <= 0:
            return False
        near_left = (cx - d.canvas_x) < b
        near_right = (d.canvas_right - cx) < b
        near_top = (cy - d.canvas_y) < b
        near_bottom = (d.canvas_bottom - cy) < b
        return (near_left or near_right) and (near_top or near_bottom)

    def find_adjacent_target(
        self,
        cur_display: DisplayRect,
        cur_cx: float,
        cur_cy: float,
        target_cx: float,
        target_cy: float,
        online_devices: Optional[set] = None,
    ) -> Optional[Tuple[DisplayRect, float, float]]:
        """
        When a movement from `(cur_cx, cur_cy)` to `(target_cx, target_cy)` leaves
        `cur_display`, check if any other online display on the canvas can receive
        the cursor across the crossed edge.
        Returns `(target_display, entry_cx, entry_cy)` if a transition is valid.
        """
        if self._in_corner_barrier(cur_display, cur_cx, cur_cy):
            return None

        tol = max(2.0, self.settings.edge_snap_tolerance_px)

        candidates = [
            d
            for d in self.displays.values()
            if d.id != cur_display.id
            and (online_devices is None or d.device_id in online_devices)
        ]
        if not candidates:
            return None

        # 1. Direct hit: target point is directly inside another display
        for d in candidates:
            if d.contains_canvas(target_cx, target_cy, eps=1.0):
                ecx = max(d.canvas_x + 1.0, min(target_cx, d.canvas_right - 1.0))
                ecy = max(d.canvas_y + 1.0, min(target_cy, d.canvas_bottom - 1.0))
                return d, ecx, ecy

        # 2. Edge-proximity transition: check which boundary was crossed
        crossed_right = target_cx > cur_display.canvas_right - 0.5 and (target_cx > cur_cx)
        crossed_left = target_cx < cur_display.canvas_x + 0.5 and (target_cx < cur_cx)
        crossed_bottom = target_cy > cur_display.canvas_bottom - 0.5 and (target_cy > cur_cy)
        crossed_top = target_cy < cur_display.canvas_y + 0.5 and (target_cy < cur_cy)

        best: Optional[Tuple[float, DisplayRect, float, float]] = None

        for d in candidates:
            # Crossing Right edge of cur_display -> Left edge of d
            if crossed_right:
                edge_gap = abs(d.canvas_x - cur_display.canvas_right)
                if edge_gap <= tol and (d.canvas_y <= target_cy <= d.canvas_bottom):
                    overshoot = max(1.0, target_cx - cur_display.canvas_right)
                    ecx = min(d.canvas_right - 1.0, d.canvas_x + overshoot)
                    ecy = max(d.canvas_y + 1.0, min(target_cy, d.canvas_bottom - 1.0))
                    if best is None or edge_gap < best[0]:
                        best = (edge_gap, d, ecx, ecy)

            # Crossing Left edge of cur_display -> Right edge of d
            if crossed_left:
                edge_gap = abs(cur_display.canvas_x - d.canvas_right)
                if edge_gap <= tol and (d.canvas_y <= target_cy <= d.canvas_bottom):
                    overshoot = max(1.0, cur_display.canvas_x - target_cx)
                    ecx = max(d.canvas_x + 1.0, d.canvas_right - overshoot)
                    ecy = max(d.canvas_y + 1.0, min(target_cy, d.canvas_bottom - 1.0))
                    if best is None or edge_gap < best[0]:
                        best = (edge_gap, d, ecx, ecy)

            # Crossing Bottom edge of cur_display -> Top edge of d
            if crossed_bottom:
                edge_gap = abs(d.canvas_y - cur_display.canvas_bottom)
                if edge_gap <= tol and (d.canvas_x <= target_cx <= d.canvas_right):
                    overshoot = max(1.0, target_cy - cur_display.canvas_bottom)
                    ecx = max(d.canvas_x + 1.0, min(target_cx, d.canvas_right - 1.0))
                    ecy = min(d.canvas_bottom - 1.0, d.canvas_y + overshoot)
                    if best is None or edge_gap < best[0]:
                        best = (edge_gap, d, ecx, ecy)

            # Crossing Top edge of cur_display -> Bottom edge of d
            if crossed_top:
                edge_gap = abs(cur_display.canvas_y - d.canvas_bottom)
                if edge_gap <= tol and (d.canvas_x <= target_cx <= d.canvas_right):
                    overshoot = max(1.0, cur_display.canvas_y - target_cy)
                    ecx = max(d.canvas_x + 1.0, min(target_cx, d.canvas_right - 1.0))
                    ecy = max(d.canvas_y + 1.0, d.canvas_bottom - overshoot)
                    if best is None or edge_gap < best[0]:
                        best = (edge_gap, d, ecx, ecy)

        if best is not None:
            _, d, ecx, ecy = best
            return d, ecx, ecy

        return None

    def step_from_native(
        self,
        cur_device_id: str,
        os_x: float,
        os_y: float,
        dx: float,
        dy: float,
        online_devices: Optional[set] = None,
    ) -> Optional[TransitionResult]:
        """
        Given current native OS cursor `(os_x, os_y)` and hardware delta `(dx, dy)`
        on `cur_device_id` (e.g. Mac), check if the cursor should transition to
        another device's display on the Virtual Canvas.
        """
        cur_display = self.find_display_by_os_point(cur_device_id, os_x, os_y)
        if cur_display is None:
            return None

        cur_cx, cur_cy = cur_display.os_to_canvas(os_x, os_y)
        # Scale delta proportionally from OS pixels to Canvas units
        scale_x = cur_display.canvas_width / max(1.0, cur_display.os_width)
        scale_y = cur_display.canvas_height / max(1.0, cur_display.os_height)

        # Only trigger a cross-device check if near the edge of cur_display or outside
        at_left = (os_x - cur_display.os_x) <= 2.0 and dx < 0
        at_right = (cur_display.os_right - os_x) <= 2.5 and dx > 0
        at_top = (os_y - cur_display.os_y) <= 2.0 and dy < 0
        at_bottom = (cur_display.os_bottom - os_y) <= 2.5 and dy > 0

        if not (at_left or at_right or at_top or at_bottom):
            ox, oy = int(round(os_x)), int(round(os_y))
            return TransitionResult(
                display=cur_display,
                canvas_x=cur_cx,
                canvas_y=cur_cy,
                os_x=ox,
                os_y=oy,
                device_changed=False,
                display_changed=False,
            )

        target_cx = cur_cx + dx * scale_x
        target_cy = cur_cy + dy * scale_y

        adj = self.find_adjacent_target(
            cur_display, cur_cx, cur_cy, target_cx, target_cy, online_devices
        )
        if adj is not None:
            next_disp, ecx, ecy = adj
            nox, noy = next_disp.canvas_to_os(ecx, ecy)
            return TransitionResult(
                display=next_disp,
                canvas_x=ecx,
                canvas_y=ecy,
                os_x=nox,
                os_y=noy,
                device_changed=(next_disp.device_id != cur_display.device_id),
                display_changed=True,
            )

        ox, oy = int(round(os_x)), int(round(os_y))
        return TransitionResult(
            display=cur_display,
            canvas_x=cur_cx,
            canvas_y=cur_cy,
            os_x=ox,
            os_y=oy,
            device_changed=False,
            display_changed=False,
        )

    def step_virtual_delta(
        self,
        cur_display_id: str,
        cur_cx: float,
        cur_cy: float,
        dx: float,
        dy: float,
        online_devices: Optional[set] = None,
    ) -> Optional[TransitionResult]:
        """
        Advance a virtual cursor currently on `cur_display_id` at `(cur_cx, cur_cy)`
        by raw delta `(dx, dy)` (when controlling a remote machine like Windows).
        Handles moving inside the display, crossing to another display on the same
        or different machine, or clamping firmly to the display boundary if no
        adjacent screen exists.
        """
        cur_display = self.displays.get(cur_display_id)
        if cur_display is None:
            return None

        speed = self.settings.mouse_speed_windows if cur_display.os_type == "windows" else 1.0
        scale_x = (cur_display.canvas_width / max(1.0, cur_display.os_width)) * speed
        scale_y = (cur_display.canvas_height / max(1.0, cur_display.os_height)) * speed

        target_cx = cur_cx + dx * scale_x
        target_cy = cur_cy + dy * scale_y

        if cur_display.contains_canvas(target_cx, target_cy, eps=-0.5):
            ox, oy = cur_display.canvas_to_os(target_cx, target_cy)
            return TransitionResult(
                display=cur_display,
                canvas_x=target_cx,
                canvas_y=target_cy,
                os_x=ox,
                os_y=oy,
                device_changed=False,
                display_changed=False,
            )

        # Outside current display: check if an adjacent display receives it
        adj = self.find_adjacent_target(
            cur_display, cur_cx, cur_cy, target_cx, target_cy, online_devices
        )
        if adj is not None:
            next_disp, ecx, ecy = adj
            nox, noy = next_disp.canvas_to_os(ecx, ecy)
            return TransitionResult(
                display=next_disp,
                canvas_x=ecx,
                canvas_y=ecy,
                os_x=nox,
                os_y=noy,
                device_changed=(next_disp.device_id != cur_display.device_id),
                display_changed=True,
            )

        # No adjacent display: clamp cleanly inside cur_display
        clamped_cx = max(cur_display.canvas_x, min(target_cx, cur_display.canvas_right))
        clamped_cy = max(cur_display.canvas_y, min(target_cy, cur_display.canvas_bottom))
        ox, oy = cur_display.canvas_to_os(clamped_cx, clamped_cy)
        return TransitionResult(
            display=cur_display,
            canvas_x=clamped_cx,
            canvas_y=clamped_cy,
            os_x=ox,
            os_y=oy,
            device_changed=False,
            display_changed=False,
        )
