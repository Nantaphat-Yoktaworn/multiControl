#!/usr/bin/env python3
"""
MultiControl Unified Desktop Agent (macOS Master & Windows Receiver).

Features:
  - Connects to the MultiControl Central Server Hub via WebSocket (auto-selecting
    between LAN IP and Tailscale IP).
  - Reports all attached displays with exact OS bounds and DPI scale factor.
  - Exchanges UDP candidate endpoints for sub-millisecond Direct P2P UDP input
    streaming between Mac and Windows, with automatic fallback to Server UDP Relay
    and WebSocket relay if a firewall blocks direct UDP.
  - Strict sequence deduplication & reliable state-packet delivery so clicks/keys
    are never duplicated or stuck.
  - Executes the 2D Virtual Canvas edge-transition engine locally on Mac for
    instantaneous, pixel-accurate cursor handoff between Mac and Windows.
  - Synchronizes clipboard text asynchronously on screen transition.
  - Provides a macOS Menu Bar / Windows System Tray icon.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
import json
import logging
import os
import platform
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Deque, Dict, List, Optional, Set, Tuple

# Ensure local imports work regardless of working directory
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from layout_engine import DisplayRect, VirtualCanvasEngine
from protocol import (
    ENTER_Screen_STRUCT,
    KEY_Event_STRUCT,
    MOUSE_Button_STRUCT,
    MOUSE_Move_STRUCT,
    MOUSE_Scroll_STRUCT,
    PING_STRUCT,
    PacketType,
    compute_token_digest,
    mac_keycode_to_win_vk,
    pack_packet,
    unpack_header,
)
from tray import TrayController

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("multicontrol.agent")


def detect_os_type() -> str:
    sys_plat = sys.platform.lower()
    if sys_plat == "darwin":
        return "darwin"
    if sys_plat in ("win32", "cygwin"):
        return "windows"
    return "linux"


def get_local_ipv4_addresses() -> List[str]:
    """Discover all local non-loopback IPv4 addresses (LAN + Tailscale)."""
    ips: List[str] = []
    for probe_target in ("192.168.1.100", "100.64.0.10", "8.8.8.8"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.2)
            s.connect((probe_target, 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
        except Exception:
            pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if (
                ip
                and not ip.startswith("127.")
                and not ip.startswith("169.254.")
                and not ip.startswith("172.")
                and ip not in ips
            ):
                ips.append(ip)
    except Exception:
        pass

    return ips


def re_sanitize(name: str) -> str:
    return (
        "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")[:20]
        or "node"
    )


def load_or_create_device_id(config_path: Path) -> Tuple[str, dict]:
    """Load config.json if present, and ensure a persistent device_id."""
    cfg: dict = {}
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}

    if not cfg.get("device_id"):
        os_prefix = detect_os_type()
        short_host = re_sanitize(platform.node() or os_prefix)
        cfg["device_id"] = f"{os_prefix}-{short_host}-{uuid.uuid4().hex[:6]}"
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception:
            pass

    return cfg["device_id"], cfg


def _seq_is_newer(seq: int, last_seq: int) -> bool:
    """Modular uint32 sequence comparison."""
    if last_seq < 0:
        return True
    diff = (seq - last_seq) & 0xFFFFFFFF
    return 0 < diff < 0x80000000


class MultiControlAgent:
    """Core runtime for both macOS Master and Windows Receiver."""

    def __init__(
        self,
        server_urls: List[str],
        token: str,
        device_id: str,
        device_name: str,
        udp_port: int = 8767,
        server_udp_port: int = 8766,
    ) -> None:
        self.os_type = detect_os_type()
        self.server_urls = server_urls
        self.token = token
        self.token_digest = compute_token_digest(token)
        self.device_id = device_id
        self.device_name = device_name
        self.udp_port = udp_port
        self.server_udp_port = server_udp_port

        self.engine = VirtualCanvasEngine()
        self.online_devices: Set[str] = {self.device_id}
        self.peer_udp_candidates: Dict[str, List[Tuple[str, int]]] = {}
        # device_id -> (ip, port, rtt_ms, updated_monotonic)
        self.best_peer_addr: Dict[str, Tuple[str, int, float, float]] = {}
        self.server_relay_addr: Optional[Tuple[str, int]] = None

        # Active cursor tracking on Virtual Canvas
        self.active_device_id: str = self.device_id
        self.active_display_id: str = f"{self.device_id}:0"
        self.cur_canvas_x: float = 400.0
        self.cur_canvas_y: float = 400.0
        self.cur_os_x: int = 400
        self.cur_os_y: int = 400
        self.screen_locked: bool = False
        self._remote_buttons_held: Set[int] = set()
        self._last_transition_ts: float = 0.0

        # Networking & deduplication state
        self._udp_sock: Optional[socket.socket] = None
        self._seq: int = 0
        self._seen_seqs: Deque[int] = deque(maxlen=512)
        self._seen_seq_set: Set[int] = set()
        self._last_move_seq: int = -1
        self._last_rx_ts: float = 0.0
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = True
        self._last_telemetry_ts: float = 0.0
        self._last_clipboard_text: str = ""
        self._clip_lock = threading.Lock()

        # Platform-specific controllers
        self.mac_ctrl = None
        self.win_injector = None

        if self.os_type == "darwin":
            from mac_backend import MacInputController
            self.mac_ctrl = MacInputController(
                on_mouse_move=self._on_mac_mouse_move,
                on_mouse_button=self._on_mac_mouse_button,
                on_mouse_scroll=self._on_mac_mouse_scroll,
                on_key_event=self._on_mac_key_event,
                on_emergency_return=self.force_return_to_mac,
                on_toggle_screen_lock=self.toggle_screen_lock,
            )
        elif self.os_type == "windows":
            from win_backend import WinInputInjector
            self.win_injector = WinInputInjector()

        web_url = self._derive_web_ui_url(self.server_urls[0])
        self.tray = TrayController(
            web_ui_url=web_url,
            is_mac=(self.os_type == "darwin"),
            on_return_to_mac=self.force_return_to_mac,
            on_toggle_lock=self.toggle_screen_lock,
            on_sync_clipboard=self.push_local_clipboard,
            on_quit=self.shutdown,
        )

    @staticmethod
    def _derive_web_ui_url(ws_url: str) -> str:
        url = ws_url.replace("wss://", "https://").replace("ws://", "http://")
        if "/ws" in url:
            url = url.split("/ws")[0]
        return url

    def get_local_displays(self) -> List[DisplayRect]:
        if self.os_type == "darwin":
            from mac_backend import get_mac_displays
            disps = get_mac_displays(self.device_id, self.device_name)
            if disps and self.mac_ctrl is not None:
                primary = next((d for d in disps if d.primary), disps[0])
                self.mac_ctrl.set_park_position(
                    primary.os_x + primary.os_width / 2.0,
                    primary.os_y + primary.os_height / 2.0,
                )
            return disps
        if self.os_type == "windows":
            from win_backend import get_win_displays
            return get_win_displays(self.device_id, self.device_name)
        return [
            DisplayRect(
                id=f"{self.device_id}:0",
                device_id=self.device_id,
                device_name=self.device_name,
                os_type=self.os_type,
                monitor_index=0,
                name=f"{self.device_name} Display",
                primary=True,
                os_x=0.0,
                os_y=0.0,
                os_width=1920.0,
                os_height=1080.0,
            )
        ]

    # ------------------------------------------------------------------
    # Asynchronous Clipboard Access (Never Blocks CGEventTap / UDPRecvLoop)
    # ------------------------------------------------------------------
    def read_local_clipboard(self) -> str:
        if self.os_type == "darwin":
            from mac_backend import get_mac_clipboard
            return get_mac_clipboard()
        if self.os_type == "windows":
            from win_backend import get_win_clipboard
            return get_win_clipboard()
        return ""

    def write_local_clipboard(self, text: str) -> None:
        if not text:
            return

        def _worker() -> None:
            with self._clip_lock:
                self._last_clipboard_text = text
                if self.os_type == "darwin":
                    from mac_backend import set_mac_clipboard
                    set_mac_clipboard(text)
                elif self.os_type == "windows":
                    from win_backend import set_win_clipboard
                    set_win_clipboard(text)

        threading.Thread(target=_worker, daemon=True).start()

    def push_local_clipboard(self) -> None:
        if not self.engine.settings.clipboard_sync:
            return

        def _worker() -> None:
            with self._clip_lock:
                text = self.read_local_clipboard()
                if text and text != self._last_clipboard_text:
                    self._last_clipboard_text = text
                    self._send_ws_json(
                        {
                            "type": "clipboard_sync",
                            "source_id": self.device_id,
                            "text": text[:65536],
                        }
                    )

        threading.Thread(target=_worker, daemon=True).start()

    # ------------------------------------------------------------------
    # UDP Socket (Direct P2P + Server Relay + Sequence Deduplication)
    # ------------------------------------------------------------------
    def _setup_udp_socket(self) -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
        try:
            sock.bind(("0.0.0.0", self.udp_port))
        except OSError:
            sock.bind(("0.0.0.0", 0))
        sock.setblocking(False)
        self._udp_sock = sock
        bound_port = sock.getsockname()[1]
        self.udp_port = bound_port
        logger.info("UDP fast-path socket listening on 0.0.0.0:%d", bound_port)

        t = threading.Thread(target=self._udp_rx_loop, name="UDPRecvLoop", daemon=True)
        t.start()
        return bound_port

    def _udp_rx_loop(self) -> None:
        """High-priority thread receiving UDP mouse/keyboard packets."""
        import select
        sock = self._udp_sock
        if sock is None:
            return

        while self._running:
            try:
                r, _, _ = select.select([sock], [], [], 0.2)
                if not r:
                    continue
                while True:
                    try:
                        data, addr = sock.recvfrom(2048)
                    except (BlockingIOError, InterruptedError, ConnectionResetError):
                        break
                    self._handle_udp_packet(data, addr)
            except Exception as exc:
                logger.debug("UDP receive error: %s", exc)

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        if self._seq == 0:
            self._seq = 1
        return self._seq

    def _record_rx_seq(self, seq: int) -> bool:
        """
        Return True if `seq` is new and record it in the ring buffer.
        Return False if `seq` was already processed (duplicate packet).
        """
        if seq in self._seen_seq_set:
            return False
        if len(self._seen_seqs) == self._seen_seqs.maxlen:
            oldest = self._seen_seqs.popleft()
            self._seen_seq_set.discard(oldest)
        self._seen_seqs.append(seq)
        self._seen_seq_set.add(seq)
        return True

    def _reset_rx_seq_window(self) -> None:
        self._seen_seqs.clear()
        self._seen_seq_set.clear()
        self._last_move_seq = -1

    def _send_udp_to_target(
        self,
        target_device_id: str,
        pkt_type: int,
        payload: bytes = b"",
        reliable: bool = False,
    ) -> None:
        """
        Send a binary packet to `target_device_id`.
        When `reliable=True` (for ENTER_SCREEN, LEAVE_SCREEN, MOUSE_BUTTON, KEY_EVENT),
        the same sequence-numbered packet is sent over both the direct P2P path and the
        Server UDP Relay so single-packet Wi-Fi loss never drops a key/button state change,
        while the receiver's sequence filter ensures it executes at most once.
        """
        if self._udp_sock is None:
            return

        seq = self._next_seq()
        raw = pack_packet(pkt_type, seq, self.token_digest, payload)

        now = time.monotonic()
        peer_info = self.best_peer_addr.get(target_device_id)
        sent_direct = False

        if peer_info is not None:
            ip, port, _, ts = peer_info
            if now - ts <= 8.0:
                try:
                    self._udp_sock.sendto(raw, (ip, port))
                    sent_direct = True
                except Exception:
                    pass
            else:
                self.best_peer_addr.pop(target_device_id, None)

        if not sent_direct:
            candidates = self.peer_udp_candidates.get(target_device_id, [])
            for ip, port in candidates[:1]:
                try:
                    self._udp_sock.sendto(raw, (ip, port))
                except Exception:
                    pass

        if (not sent_direct or reliable) and self.server_relay_addr is not None:
            try:
                self._udp_sock.sendto(raw, self.server_relay_addr)
            except Exception:
                pass

    def _probe_peers_udp(self) -> None:
        """Send UDP PING to all peer candidate IPs and register with Server UDP relay."""
        if self._udp_sock is None:
            return

        now_ns = time.time_ns()
        ping_payload = PING_STRUCT.pack(now_ns) + self.device_id.encode("utf-8")
        ping_pkt = pack_packet(
            PacketType.PING, self._next_seq(), self.token_digest, ping_payload
        )

        for dev_id, candidates in list(self.peer_udp_candidates.items()):
            if dev_id == self.device_id:
                continue
            for ip, port in candidates:
                try:
                    self._udp_sock.sendto(ping_pkt, (ip, port))
                except Exception:
                    pass

        if self.server_relay_addr is not None:
            hello_payload = self.device_id.encode("utf-8")
            hello_pkt = pack_packet(
                PacketType.HELLO, self._next_seq(), self.token_digest, hello_payload
            )
            try:
                self._udp_sock.sendto(hello_pkt, self.server_relay_addr)
            except Exception:
                pass

    def _handle_udp_packet(self, data: bytes, addr: Tuple[str, int]) -> None:
        parsed = unpack_header(data)
        if parsed is None:
            return
        pkt_type, seq, token_digest, payload = parsed
        if token_digest != self.token_digest:
            return

        if pkt_type == PacketType.PING:
            if len(payload) >= PING_STRUCT.size:
                pong = pack_packet(
                    PacketType.PONG,
                    self._next_seq(),
                    self.token_digest,
                    payload[: PING_STRUCT.size] + self.device_id.encode("utf-8"),
                )
                try:
                    if self._udp_sock:
                        self._udp_sock.sendto(pong, addr)
                except Exception:
                    pass
            return

        if pkt_type == PacketType.PONG:
            if len(payload) > PING_STRUCT.size:
                sent_ns = PING_STRUCT.unpack_from(payload, 0)[0]
                peer_id = payload[PING_STRUCT.size :].decode("utf-8", errors="ignore")
                rtt_ms = max(0.05, (time.time_ns() - sent_ns) / 1_000_000.0)
                now = time.monotonic()
                prev = self.best_peer_addr.get(peer_id)
                if prev is None or (now - prev[3] > 5.0) or rtt_ms <= prev[2] + 1.5:
                    addr_changed = prev is None or (prev[0], prev[1]) != (addr[0], addr[1])
                    self.best_peer_addr[peer_id] = (addr[0], addr[1], rtt_ms, now)
                    if addr_changed:
                        self.tray.update_status(
                            status_text=self.tray.status_text,
                            transport_text=f"Direct P2P UDP ({addr[0]}:{addr[1]}, {rtt_ms:.1f}ms)",
                        )
            return

        # Receiver side (Windows PC): execute incoming input events!
        if self.win_injector is None:
            return

        now = time.monotonic()
        if now - self._last_rx_ts > 1.5:
            self._reset_rx_seq_window()
        self._last_rx_ts = now

        if pkt_type == PacketType.MOUSE_MOVE and len(payload) >= MOUSE_Move_STRUCT.size:
            if not self._record_rx_seq(seq):
                return
            if not _seq_is_newer(seq, self._last_move_seq):
                return
            self._last_move_seq = seq
            x, y = MOUSE_Move_STRUCT.unpack_from(payload, 0)
            self.win_injector.move_cursor(x, y)
            return

        if pkt_type == PacketType.ENTER_SCREEN and len(payload) >= ENTER_Screen_STRUCT.size:
            if not self._record_rx_seq(seq):
                return
            self._last_move_seq = seq
            x, y = ENTER_Screen_STRUCT.unpack_from(payload, 0)
            self.win_injector.move_cursor(x, y)
            self.tray.update_status(
                status_text="Active — Controlled by Mac",
                state="remote",
            )
            return

        if pkt_type == PacketType.LEAVE_SCREEN:
            if not self._record_rx_seq(seq):
                return
            self._reset_rx_seq_window()
            self.win_injector.leave_screen()
            self.tray.update_status(
                status_text="Standby — Cursor on Mac",
                state="connected",
            )
            self.push_local_clipboard()
            return

        if pkt_type == PacketType.MOUSE_BUTTON and len(payload) >= MOUSE_Button_STRUCT.size:
            if not self._record_rx_seq(seq):
                return
            btn, state, x, y = MOUSE_Button_STRUCT.unpack_from(payload, 0)
            self.win_injector.move_cursor(x, y)
            self.win_injector.mouse_button(btn, state)
            return

        if pkt_type == PacketType.MOUSE_SCROLL and len(payload) >= MOUSE_Scroll_STRUCT.size:
            if not self._record_rx_seq(seq):
                return
            dx, dy, is_pixel = MOUSE_Scroll_STRUCT.unpack_from(payload, 0)
            self.win_injector.mouse_scroll(
                dx,
                dy,
                bool(is_pixel),
                scroll_speed=self.engine.settings.scroll_speed,
                invert=self.engine.settings.invert_scroll_on_windows,
            )
            return

        if pkt_type == PacketType.KEY_EVENT and len(payload) >= KEY_Event_STRUCT.size:
            if not self._record_rx_seq(seq):
                return
            vk, state, flags = KEY_Event_STRUCT.unpack_from(payload, 0)
            self.win_injector.key_event(vk, state, flags)
            return

    # ------------------------------------------------------------------
    # macOS Master Input Callbacks & Seamless Transition Logic
    # ------------------------------------------------------------------
    def force_return_to_mac(self) -> None:
        """Emergency hotkey or menu action: immediately bring cursor back to Mac."""
        if self.os_type != "darwin" or self.mac_ctrl is None:
            return

        prev_target = self.active_device_id
        self.active_device_id = self.device_id
        self.screen_locked = False
        self._remote_buttons_held.clear()
        self._last_transition_ts = time.monotonic()

        mac_displays = [
            d for d in self.engine.displays.values() if d.device_id == self.device_id
        ]
        if mac_displays:
            primary = next((d for d in mac_displays if d.primary), mac_displays[0])
            cx = primary.os_x + primary.os_width / 2.0
            cy = primary.os_y + primary.os_height / 2.0
            self.active_display_id = primary.id
            self.cur_canvas_x, self.cur_canvas_y = primary.os_to_canvas(cx, cy)
            self.mac_ctrl.leave_remote_mode(cx, cy)
        else:
            self.mac_ctrl.leave_remote_mode(500.0, 500.0)

        if prev_target != self.device_id:
            self._send_udp_to_target(prev_target, PacketType.LEAVE_SCREEN, reliable=True)
            self._send_ws_json({"type": "leave_screen", "target_id": prev_target})

        self.tray.update_status(
            status_text="Controlling Mac",
            state="connected",
            screen_locked=False,
        )
        logger.info("Returned cursor control to Mac.")

    def toggle_screen_lock(self) -> None:
        """Lock/unlock the cursor to whichever screen it is currently on."""
        self.screen_locked = not self.screen_locked
        if self.mac_ctrl is not None:
            self.mac_ctrl.screen_locked = self.screen_locked
        target_label = "Mac" if self.active_device_id == self.device_id else "Windows PC"
        state_label = (
            f"Locked to {target_label}"
            if self.screen_locked
            else f"Controlling {target_label}"
        )
        self.tray.update_status(
            status_text=state_label,
            state="remote" if self.active_device_id != self.device_id else "connected",
            screen_locked=self.screen_locked,
        )

    def _on_mac_mouse_move(
        self, ox: float, oy: float, dx: float, dy: float, is_dragging: bool = False
    ) -> None:
        """Called on every mouse movement on the Mac."""
        if self.mac_ctrl is None:
            return

        now = time.monotonic()
        in_cooldown = (now - self._last_transition_ts) < 0.05

        # Case 1: Cursor is currently on Mac
        drag_lock = self.engine.settings.lock_to_screen_while_dragging
        if not self.mac_ctrl.remote_active:
            allow_transition = not (
                self.screen_locked or (drag_lock and is_dragging) or in_cooldown
            )

            res = self.engine.step_from_native(
                cur_device_id=self.device_id,
                os_x=ox,
                os_y=oy,
                dx=dx,
                dy=dy,
                online_devices=self.online_devices,
                allow_transition=allow_transition,
            )
            if res is None:
                return

            self.active_display_id = res.display.id
            self.cur_canvas_x = res.canvas_x
            self.cur_canvas_y = res.canvas_y
            self.cur_os_x = res.os_x
            self.cur_os_y = res.os_y

            if res.device_changed and res.display.device_id != self.device_id:
                # Seamlessly transition from Mac -> Windows!
                self.active_device_id = res.display.device_id
                self._remote_buttons_held.clear()
                self._last_transition_ts = now
                self.mac_ctrl.enter_remote_mode()

                # Send ENTER_SCREEN UDP packet immediately first, then sync clipboard in background
                payload = ENTER_Screen_STRUCT.pack(res.os_x, res.os_y)
                self._send_udp_to_target(
                    self.active_device_id,
                    PacketType.ENTER_SCREEN,
                    payload,
                    reliable=True,
                )
                self.push_local_clipboard()
                self.tray.update_status(
                    status_text=f"Controlling {res.display.device_name}",
                    state="remote",
                )
                logger.info(
                    "Cursor crossed Mac -> %s (%s) at OS (%d, %d)",
                    res.display.device_name,
                    res.display.name,
                    res.os_x,
                    res.os_y,
                )

            self._emit_cursor_telemetry(self.cur_os_x, self.cur_os_y)
            return

        # Case 2: Cursor is currently controlling Windows (remote_active == True)
        # Do not jump back to Mac while dragging a window/selection on Windows or during 50ms cooldown
        lock_to_remote = (
            self.screen_locked
            or (drag_lock and bool(self._remote_buttons_held))
            or in_cooldown
        )
        allowed_devices = (
            {self.active_device_id} if lock_to_remote else self.online_devices
        )
        res = self.engine.step_virtual_delta(
            cur_display_id=self.active_display_id,
            cur_cx=self.cur_canvas_x,
            cur_cy=self.cur_canvas_y,
            dx=dx,
            dy=dy,
            online_devices=allowed_devices,
        )
        if res is None:
            self.force_return_to_mac()
            return

        prev_device_id = self.active_device_id
        self.active_display_id = res.display.id
        self.active_device_id = res.display.device_id
        self.cur_canvas_x = res.canvas_x
        self.cur_canvas_y = res.canvas_y
        self.cur_os_x = res.os_x
        self.cur_os_y = res.os_y

        if self.active_device_id == self.device_id:
            # Cursor crossed back from Windows -> Mac at exact entry coordinate!
            self._remote_buttons_held.clear()
            self._last_transition_ts = now
            self.mac_ctrl.leave_remote_mode(res.os_x, res.os_y)
            self._send_udp_to_target(
                prev_device_id, PacketType.LEAVE_SCREEN, reliable=True
            )
            self._send_ws_json({"type": "leave_screen", "target_id": prev_device_id})
            self.tray.update_status(
                status_text="Controlling Mac",
                state="connected",
            )
            logger.info(
                "Cursor returned %s -> Mac (%s) at OS (%d, %d)",
                prev_device_id,
                res.display.name,
                res.os_x,
                res.os_y,
            )
        else:
            payload = MOUSE_Move_STRUCT.pack(res.os_x, res.os_y)
            self._send_udp_to_target(
                self.active_device_id, PacketType.MOUSE_MOVE, payload
            )

        self._emit_cursor_telemetry(self.cur_os_x, self.cur_os_y)

    def _on_mac_mouse_button(self, button: int, state: int) -> None:
        if self.active_device_id == self.device_id:
            return
        if state == 1:
            self._remote_buttons_held.add(button)
        else:
            self._remote_buttons_held.discard(button)
        payload = MOUSE_Button_STRUCT.pack(button, state, self.cur_os_x, self.cur_os_y)
        self._send_udp_to_target(
            self.active_device_id,
            PacketType.MOUSE_BUTTON,
            payload,
            reliable=True,
        )

    def _on_mac_mouse_scroll(self, dx: float, dy: float, is_continuous: bool) -> None:
        if self.active_device_id == self.device_id:
            return
        payload = MOUSE_Scroll_STRUCT.pack(
            float(dx), float(dy), 1 if is_continuous else 0
        )
        self._send_udp_to_target(self.active_device_id, PacketType.MOUSE_SCROLL, payload)

    def _on_mac_key_event(self, mac_keycode: int, state: int) -> None:
        if self.active_device_id == self.device_id:
            return
        mapped = mac_keycode_to_win_vk(
            mac_keycode,
            swap_cmd_ctrl=self.engine.settings.swap_cmd_ctrl,
            swap_opt_win=self.engine.settings.swap_opt_win,
        )
        if mapped is None:
            return
        win_vk, flags = mapped
        payload = KEY_Event_STRUCT.pack(win_vk, state, flags)
        self._send_udp_to_target(
            self.active_device_id,
            PacketType.KEY_EVENT,
            payload,
            reliable=True,
        )

    def _emit_cursor_telemetry(self, os_x: float, os_y: float) -> None:
        """Send lightweight cursor telemetry (~25 Hz) to the server."""
        now = time.monotonic()
        if now - self._last_telemetry_ts < 0.04:
            return
        self._last_telemetry_ts = now
        self._send_ws_json(
            {
                "type": "cursor_telemetry",
                "device_id": self.active_device_id,
                "display_id": self.active_display_id,
                "canvas_x": round(self.cur_canvas_x, 1),
                "canvas_y": round(self.cur_canvas_y, 1),
                "os_x": int(round(os_x)),
                "os_y": int(round(os_y)),
            }
        )

    # ------------------------------------------------------------------
    # WebSocket Control Plane Loop
    # ------------------------------------------------------------------
    def _send_ws_json(self, msg: dict) -> None:
        if self._ws is None or self._loop is None:
            return
        raw = json.dumps(msg)
        asyncio.run_coroutine_threadsafe(self._async_ws_send(raw), self._loop)

    async def _async_ws_send(self, raw: str) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(raw)
            except Exception:
                pass

    async def _ws_main_loop(self) -> None:
        import websockets
        from urllib.parse import urlparse

        self._loop = asyncio.get_running_loop()
        url_idx = 0

        while self._running:
            ws_url = self.server_urls[url_idx % len(self.server_urls)]
            url_idx += 1

            parsed = urlparse(ws_url)
            server_host = parsed.hostname or "192.168.1.100"
            self.server_relay_addr = (server_host, self.server_udp_port)

            try:
                logger.info("Connecting to MultiControl Server Hub: %s", ws_url)
                async with websockets.connect(
                    ws_url,
                    ping_interval=15,
                    ping_timeout=15,
                    close_timeout=3,
                ) as ws:
                    self._ws = ws
                    local_ips = get_local_ipv4_addresses()
                    displays = [d.to_dict() for d in self.get_local_displays()]

                    reg_msg = {
                        "type": "register",
                        "token": self.token,
                        "device_id": self.device_id,
                        "device_name": self.device_name,
                        "os_type": self.os_type,
                        "ips": local_ips,
                        "udp_port": self.udp_port,
                        "displays": displays,
                    }
                    await ws.send(json.dumps(reg_msg))
                    self.tray.update_status(
                        status_text="Connected to Hub",
                        transport_text=f"Hub: {server_host}",
                        state="connected",
                    )

                    probe_task = asyncio.create_task(self._periodic_probe_task())
                    try:
                        async for raw_msg in ws:
                            data = json.loads(raw_msg)
                            self._handle_ws_message(data)
                    finally:
                        probe_task.cancel()
                        self._ws = None
            except Exception as exc:
                self._ws = None
                self.tray.update_status(
                    status_text="Reconnecting to Server...",
                    transport_text=str(exc)[:40],
                    state="disconnected",
                )
                logger.warning("Server connection lost (%s), retrying in 2s...", exc)
                await asyncio.sleep(2.0)

    async def _periodic_probe_task(self) -> None:
        while self._running:
            self._probe_peers_udp()
            await asyncio.sleep(3.0)

    def _handle_ws_message(self, data: dict) -> None:
        msg_type = data.get("type")

        if msg_type == "cluster_state":
            self.engine.update_from_dict(data)
            if self.mac_ctrl is not None:
                s = self.engine.settings
                self.mac_ctrl.update_feature_flags(
                    three_finger_middle_click=s.three_finger_middle_click,
                    three_finger_swipe_windows=s.three_finger_swipe_windows,
                    suppress_mac_mission_control=s.suppress_mac_mission_control,
                    forward_media_keys=s.forward_media_keys,
                )
            devices = data.get("devices", [])
            online_set: Set[str] = {self.device_id}
            new_candidates: Dict[str, List[Tuple[str, int]]] = {}

            for dev in devices:
                dev_id = dev.get("device_id")
                if not dev_id:
                    continue
                if dev.get("online"):
                    online_set.add(dev_id)
                else:
                    self.best_peer_addr.pop(dev_id, None)
                uport = int(dev.get("udp_port", 8767))
                cands = [(ip, uport) for ip in dev.get("ips", []) if ip]
                new_candidates[dev_id] = cands

            self.online_devices = online_set
            self.peer_udp_candidates = new_candidates

            if (
                self.os_type == "darwin"
                and self.active_device_id != self.device_id
                and self.active_device_id not in self.online_devices
            ):
                logger.warning("Remote target disconnected; returning cursor to Mac.")
                self.force_return_to_mac()

            self._probe_peers_udp()
            return

        if msg_type == "clipboard_sync":
            if not self.engine.settings.clipboard_sync:
                return
            if data.get("source_id") != self.device_id:
                text = data.get("text", "")
                if text and text != self._last_clipboard_text:
                    self.write_local_clipboard(text)
            return

        if msg_type in ("force_return_mac", "leave_screen"):
            if self.os_type == "darwin":
                self.force_return_to_mac()
            elif self.win_injector is not None:
                self._reset_rx_seq_window()
                self.win_injector.leave_screen()
                self.tray.update_status(
                    status_text="Standby — Cursor on Mac",
                    state="connected",
                )
                self.push_local_clipboard()
            return

        if msg_type == "refresh_displays":
            displays = [d.to_dict() for d in self.get_local_displays()]
            self._send_ws_json(
                {
                    "type": "update_displays",
                    "device_id": self.device_id,
                    "displays": displays,
                }
            )

    # ------------------------------------------------------------------
    # Startup & Lifecycle
    # ------------------------------------------------------------------
    def start(self, headless: bool = False) -> None:
        self._setup_udp_socket()

        if self.os_type == "darwin" and self.mac_ctrl is not None:
            self.get_local_displays()
            started = self.mac_ctrl.start()
            if started:
                logger.info(
                    "macOS Quartz EventTap active! Emergency return hotkey: Ctrl+Opt+Esc"
                )
            else:
                logger.warning(
                    "macOS EventTap waiting for Accessibility permission. "
                    "Grant permission in System Settings -> Privacy & Security -> Accessibility."
                )

        ws_thread = threading.Thread(
            target=lambda: asyncio.run(self._ws_main_loop()),
            name="WSControlPlane",
            daemon=True,
        )
        ws_thread.start()

        if not headless:
            ran_tray = self.tray.run_blocking()
            if ran_tray:
                return

        logger.info("MultiControl Agent running in console mode (Press Ctrl+C to stop).")
        try:
            while self._running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self) -> None:
        logger.info("Shutting down MultiControl Agent...")
        self._running = False
        if self.mac_ctrl is not None:
            self.mac_ctrl.stop()
        if self.win_injector is not None:
            self.win_injector.leave_screen()
        self.tray.stop()
        os._exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MultiControl Desktop Agent (Mac & Windows)"
    )
    parser.add_argument(
        "--config",
        default=str(SCRIPT_DIR / "config.json"),
        help="Path to config.json",
    )
    parser.add_argument(
        "--server",
        default=None,
        help="Server WebSocket URL (e.g. ws://192.168.1.100:8765/ws/agent)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Pairing token",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Display name for this computer",
    )
    parser.add_argument(
        "--udp-port",
        type=int,
        default=8767,
        help="Local UDP port for P2P input streaming (default: 8767)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without system tray / menu bar icon",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    device_id, cfg = load_or_create_device_id(config_path)

    server_urls: List[str] = []
    if args.server:
        server_urls.append(args.server)
    if cfg.get("server_urls"):
        for u in cfg["server_urls"]:
            if u not in server_urls:
                server_urls.append(u)
    if not server_urls:
        server_urls = [
            "ws://192.168.1.100:8765/ws/agent",
            "ws://100.64.0.10:8765/ws/agent",
        ]

    token = args.token or cfg.get("token") or "mc-default-pair-token"
    default_name = (
        f"{platform.node()} ({'Mac' if detect_os_type() == 'darwin' else 'Windows'})"
    )
    device_name = args.name or cfg.get("device_name") or default_name
    udp_port = int(cfg.get("udp_port", args.udp_port))
    server_udp_port = int(cfg.get("server_udp_port", 8766))

    agent = MultiControlAgent(
        server_urls=server_urls,
        token=token,
        device_id=device_id,
        device_name=device_name,
        udp_port=udp_port,
        server_udp_port=server_udp_port,
    )
    agent.start(headless=args.headless)


if __name__ == "__main__":
    main()
