"""
MultiControl Central Hub, 2D Layout Studio API, UDP Relay & Client Bootstrap Server.

Runs on:
  - TCP 8765: Web UI, REST API, WebSocket Control Plane (/ws/agent, /ws/ui),
              1-line installers (/install.sh, /install.ps1, /download/client.zip)
  - UDP 8766: Sub-millisecond UDP packet relay fallback when direct P2P UDP
              between Mac and Windows is blocked by a firewall.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import secrets
import sys
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

ROOT_DIR = Path(__file__).resolve().parent.parent
CLIENT_DIR = ROOT_DIR / "client"
STATIC_DIR = Path(__file__).resolve().parent / "static"
DATA_DIR = Path(os.environ.get("MULTICONTROL_DATA_DIR", str(ROOT_DIR / "data")))

if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))

from layout_engine import DisplayRect, LayoutSettings, VirtualCanvasEngine
from protocol import PacketType, compute_token_digest, unpack_header

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("multicontrol.server")

LAN_IP = os.environ.get("MULTICONTROL_LAN_IP", "192.168.1.100")
TAILSCALE_IP = os.environ.get("MULTICONTROL_TAILSCALE_IP", "100.64.0.10")
HTTP_PORT = int(os.environ.get("MULTICONTROL_PORT", "8765"))
UDP_RELAY_PORT = int(os.environ.get("MULTICONTROL_UDP_PORT", "8766"))


class HubState:
    """Manages persistent layout configuration, connected devices, and WebSockets."""

    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        self.token: str = os.environ.get("MULTICONTROL_TOKEN", "")
        self.require_token: bool = True
        self.settings: LayoutSettings = LayoutSettings()
        self.displays: Dict[str, DisplayRect] = {}
        self.devices: Dict[str, dict] = {}
        self.agent_sockets: Dict[str, WebSocket] = {}
        self.ui_sockets: Set[WebSocket] = set()
        self.udp_endpoints: Dict[str, Tuple[str, int]] = {}  # device_id -> (ip, port)

        self.cursor_state: dict = {
            "device_id": "",
            "display_id": "",
            "canvas_x": 400.0,
            "canvas_y": 400.0,
            "os_x": 400,
            "os_y": 400,
            "updated_at": 0.0,
        }

        self.load()

    @property
    def token_digest(self) -> bytes:
        return compute_token_digest(self.token)

    def load(self) -> None:
        if self.state_file.exists():
            try:
                raw = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.token = self.token or raw.get("token") or f"mc-{secrets.token_hex(8)}"
                self.require_token = bool(raw.get("require_token", True))
                self.settings = LayoutSettings.from_dict(raw.get("settings", {}))
                for d_raw in raw.get("displays", []):
                    d = DisplayRect.from_dict(d_raw)
                    self.displays[d.id] = d
                for dev_id, dev_info in raw.get("devices", {}).items():
                    dev_info["online"] = False
                    self.devices[dev_id] = dev_info
                logger.info(
                    "Loaded state from %s (%d displays, %d devices)",
                    self.state_file,
                    len(self.displays),
                    len(self.devices),
                )
                return
            except Exception as exc:
                logger.warning("Failed to load state.json: %s", exc)

        if not self.token:
            self.token = f"mc-{secrets.token_hex(8)}"
        # Seed a default Mac + Windows layout so the user can see & adjust screens right away
        self._seed_default_displays()
        self.save()

    def _seed_default_displays(self) -> None:
        mac_id = "darwin-macbook"
        win_id = "windows-pc"
        self.devices[mac_id] = {
            "device_id": mac_id,
            "device_name": "Mac (Master)",
            "os_type": "darwin",
            "ips": [],
            "udp_port": 8767,
            "online": False,
            "placeholder": True,
            "last_seen": 0,
        }
        self.devices[win_id] = {
            "device_id": win_id,
            "device_name": "Windows PC",
            "os_type": "windows",
            "ips": [],
            "udp_port": 8767,
            "online": False,
            "placeholder": True,
            "last_seen": 0,
        }
        self.displays[f"{mac_id}:0"] = DisplayRect(
            id=f"{mac_id}:0",
            device_id=mac_id,
            device_name="Mac (Master)",
            os_type="darwin",
            monitor_index=0,
            name="Mac Built-in Display",
            primary=True,
            os_x=0.0,
            os_y=0.0,
            os_width=1512.0,
            os_height=982.0,
            scale_factor=2.0,
            canvas_x=0.0,
            canvas_y=98.0,
            canvas_width=1512.0,
            canvas_height=982.0,
        )
        self.displays[f"{win_id}:0"] = DisplayRect(
            id=f"{win_id}:0",
            device_id=win_id,
            device_name="Windows PC",
            os_type="windows",
            monitor_index=0,
            name="Windows Main Display",
            primary=True,
            os_x=0.0,
            os_y=0.0,
            os_width=1920.0,
            os_height=1080.0,
            scale_factor=1.0,
            canvas_x=1512.0,
            canvas_y=0.0,
            canvas_width=1920.0,
            canvas_height=1080.0,
        )

    def save(self) -> None:
        data = {
            "token": self.token,
            "require_token": self.require_token,
            "settings": self.settings.to_dict(),
            "displays": [d.to_dict() for d in self.displays.values()],
            "devices": self.devices,
        }
        try:
            self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.error("Failed to save state.json: %s", exc)

    def register_device(self, msg: dict, client_ip: str) -> None:
        dev_id = str(msg["device_id"])
        dev_name = str(msg.get("device_name", dev_id))
        os_type = str(msg.get("os_type", "unknown"))
        ips = list(msg.get("ips", []))
        if client_ip and not client_ip.startswith("127.") and client_ip not in ips:
            ips.append(client_ip)
        udp_port = int(msg.get("udp_port", 8767))

        # If a placeholder device for the same OS exists and hasn't been replaced yet,
        # inherit its customized canvas position!
        placeholder_ids = [
            pid
            for pid, pinfo in list(self.devices.items())
            if pinfo.get("placeholder") and pinfo.get("os_type") == os_type and pid != dev_id
        ]
        inherited_canvas: Dict[int, Tuple[float, float, float, float]] = {}
        for pid in placeholder_ids:
            for did, disp in list(self.displays.items()):
                if disp.device_id == pid:
                    inherited_canvas[disp.monitor_index] = (
                        disp.canvas_x,
                        disp.canvas_y,
                        disp.canvas_width,
                        disp.canvas_height,
                    )
                    del self.displays[did]
            del self.devices[pid]

        self.devices[dev_id] = {
            "device_id": dev_id,
            "device_name": dev_name,
            "os_type": os_type,
            "ips": ips,
            "udp_port": udp_port,
            "online": True,
            "placeholder": False,
            "last_seen": time.time(),
        }

        incoming_displays = msg.get("displays", [])
        self._merge_device_displays(dev_id, dev_name, os_type, incoming_displays, inherited_canvas)
        self.save()

    def _merge_device_displays(
        self,
        dev_id: str,
        dev_name: str,
        os_type: str,
        incoming_displays: List[dict],
        inherited_canvas: Optional[Dict[int, Tuple[float, float, float, float]]] = None,
    ) -> None:
        inherited_canvas = inherited_canvas or {}

        # Calculate current rightmost edge of other devices so new displays don't overlap
        other_displays = [d for d in self.displays.values() if d.device_id != dev_id]
        rightmost_edge = max((d.canvas_right for d in other_displays), default=0.0)

        seen_ids: Set[str] = set()
        for raw_d in incoming_displays:
            new_d = DisplayRect.from_dict(raw_d)
            new_d.device_id = dev_id
            new_d.device_name = dev_name
            new_d.os_type = os_type
            seen_ids.add(new_d.id)

            existing = self.displays.get(new_d.id)
            if existing is not None:
                # Preserve user's custom canvas position & physical size calibration!
                new_d.canvas_x = existing.canvas_x
                new_d.canvas_y = existing.canvas_y
                # If OS resolution changed and canvas wasn't custom-scaled, update it
                if existing.canvas_width == existing.os_width:
                    new_d.canvas_width = new_d.os_width
                else:
                    new_d.canvas_width = existing.canvas_width
                if existing.canvas_height == existing.os_height:
                    new_d.canvas_height = new_d.os_height
                else:
                    new_d.canvas_height = existing.canvas_height
            elif new_d.monitor_index in inherited_canvas:
                cx, cy, cw, ch = inherited_canvas[new_d.monitor_index]
                new_d.canvas_x = cx
                new_d.canvas_y = cy
                # Keep user's position, and if placeholder had default dimensions, use real OS size
                new_d.canvas_width = new_d.os_width
                new_d.canvas_height = new_d.os_height
                # Re-snap adjacent displays if placeholder width changed
                if os_type == "darwin":
                    for od in other_displays:
                        if abs(od.canvas_x - cw) <= 24.0:
                            od.canvas_x = new_d.canvas_x + new_d.canvas_width
            else:
                # Brand new display: place neatly next to existing screens
                if other_displays:
                    new_d.canvas_x = rightmost_edge + new_d.os_x
                    new_d.canvas_y = new_d.os_y
                else:
                    new_d.canvas_x = new_d.os_x
                    new_d.canvas_y = new_d.os_y
                new_d.canvas_width = new_d.os_width
                new_d.canvas_height = new_d.os_height

            self.displays[new_d.id] = new_d

        # Remove stale displays from this device that are no longer connected
        for did in [k for k, v in self.displays.items() if v.device_id == dev_id and k not in seen_ids]:
            del self.displays[did]

    def to_cluster_payload(self) -> dict:
        return {
            "type": "cluster_state",
            "settings": self.settings.to_dict(),
            "displays": [d.to_dict() for d in self.displays.values()],
            "devices": list(self.devices.values()),
            "cursor": self.cursor_state,
            "server_info": {
                "lan_ip": LAN_IP,
                "tailscale_ip": TAILSCALE_IP,
                "http_port": HTTP_PORT,
                "udp_port": UDP_RELAY_PORT,
                "require_token": self.require_token,
            },
        }

    async def broadcast_state(self) -> None:
        payload_str = json.dumps(self.to_cluster_payload())
        for dev_id, ws in list(self.agent_sockets.items()):
            try:
                await ws.send_text(payload_str)
            except Exception:
                self.agent_sockets.pop(dev_id, None)

        for ws in list(self.ui_sockets):
            try:
                await ws.send_text(payload_str)
            except Exception:
                self.ui_sockets.discard(ws)

    async def broadcast_to_ui(self, msg: dict) -> None:
        if not self.ui_sockets:
            return
        raw = json.dumps(msg)
        for ws in list(self.ui_sockets):
            try:
                await ws.send_text(raw)
            except Exception:
                self.ui_sockets.discard(ws)


hub = HubState(DATA_DIR / "state.json")


class UdpRelayProtocol(asyncio.DatagramProtocol):
    """Low-latency UDP Relay fallback on port 8766."""

    def __init__(self, hub_state: HubState) -> None:
        self.hub = hub_state
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore
        logger.info("UDP Fast-Path Relay listening on 0.0.0.0:%d", UDP_RELAY_PORT)

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        parsed = unpack_header(data)
        if parsed is None:
            return
        pkt_type, seq, token_digest, payload = parsed
        if self.hub.require_token and token_digest != self.hub.token_digest:
            return

        if pkt_type == PacketType.HELLO:
            dev_id = payload.decode("utf-8", errors="ignore").strip()
            if dev_id:
                self.hub.udp_endpoints[dev_id] = addr
            return

        # Forward input packets from Mac Master to all online Windows receivers
        if self.transport is None:
            return
        for dev_id, dev_info in self.hub.devices.items():
            if not dev_info.get("online"):
                continue
            if dev_info.get("os_type") == "windows":
                target_addr = self.hub.udp_endpoints.get(dev_id)
                if target_addr and target_addr != addr:
                    self.transport.sendto(data, target_addr)
                else:
                    # Try known LAN IP + udp_port
                    ips = dev_info.get("ips", [])
                    uport = int(dev_info.get("udp_port", 8767))
                    if ips:
                        self.transport.sendto(data, (ips[0], uport))


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    transport = None
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: UdpRelayProtocol(hub),
            local_addr=("0.0.0.0", UDP_RELAY_PORT),
        )
    except Exception as exc:
        logger.warning("Could not bind UDP relay on %d: %s", UDP_RELAY_PORT, exc)
    yield
    if transport is not None:
        transport.close()


app = FastAPI(title="MultiControl Layout Studio & Hub", lifespan=lifespan)


# ---------------------------------------------------------------------------
# WebSocket Endpoints (/ws/agent and /ws/ui)
# ---------------------------------------------------------------------------
@app.websocket("/ws/agent")
async def ws_agent_endpoint(websocket: WebSocket):
    await websocket.accept()
    device_id: Optional[str] = None
    client_ip = websocket.client.host if websocket.client else ""

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "register":
                if hub.require_token and msg.get("token") != hub.token:
                    logger.warning("Rejected agent with invalid token from %s", client_ip)
                    await websocket.close(code=4001, reason="Invalid pairing token")
                    return
                device_id = str(msg["device_id"])
                hub.agent_sockets[device_id] = websocket
                hub.register_device(msg, client_ip)
                logger.info(
                    "Device registered: %s (%s) from %s",
                    msg.get("device_name"),
                    msg.get("os_type"),
                    client_ip,
                )
                await hub.broadcast_state()

            elif msg_type == "update_displays" and device_id:
                dev_info = hub.devices.get(device_id, {})
                hub._merge_device_displays(
                    device_id,
                    dev_info.get("device_name", device_id),
                    dev_info.get("os_type", "unknown"),
                    msg.get("displays", []),
                )
                hub.save()
                await hub.broadcast_state()

            elif msg_type == "cursor_telemetry":
                hub.cursor_state = {
                    "device_id": msg.get("device_id", ""),
                    "display_id": msg.get("display_id", ""),
                    "canvas_x": msg.get("canvas_x", 0.0),
                    "canvas_y": msg.get("canvas_y", 0.0),
                    "os_x": msg.get("os_x", 0),
                    "os_y": msg.get("os_y", 0),
                    "updated_at": time.time(),
                }
                await hub.broadcast_to_ui({"type": "cursor_telemetry", "cursor": hub.cursor_state})

            elif msg_type == "clipboard_sync":
                # Broadcast clipboard update to all other online agents
                payload_str = json.dumps(msg)
                for other_id, other_ws in list(hub.agent_sockets.items()):
                    if other_id != device_id:
                        try:
                            await other_ws.send_text(payload_str)
                        except Exception:
                            pass

            elif msg_type == "leave_screen":
                target_id = msg.get("target_id")
                for other_id, other_ws in list(hub.agent_sockets.items()):
                    if other_id != device_id and (not target_id or other_id == target_id):
                        try:
                            await other_ws.send_text(json.dumps({"type": "leave_screen"}))
                        except Exception:
                            pass

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("Agent WebSocket closed: %s", exc)
    finally:
        if device_id:
            if hub.agent_sockets.get(device_id) is websocket:
                hub.agent_sockets.pop(device_id, None)
            if device_id in hub.devices:
                hub.devices[device_id]["online"] = False
                hub.devices[device_id]["last_seen"] = time.time()
            await hub.broadcast_state()


@app.websocket("/ws/ui")
async def ws_ui_endpoint(websocket: WebSocket):
    await websocket.accept()
    hub.ui_sockets.add(websocket)
    try:
        await websocket.send_text(json.dumps(hub.to_cluster_payload()))
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "save_layout":
                for d_raw in msg.get("displays", []):
                    did = d_raw.get("id")
                    if did and did in hub.displays:
                        disp = hub.displays[did]
                        disp.canvas_x = round(float(d_raw.get("canvas_x", disp.canvas_x)), 1)
                        disp.canvas_y = round(float(d_raw.get("canvas_y", disp.canvas_y)), 1)
                        disp.canvas_width = max(
                            200.0, round(float(d_raw.get("canvas_width", disp.canvas_width)), 1)
                        )
                        disp.canvas_height = max(
                            150.0, round(float(d_raw.get("canvas_height", disp.canvas_height)), 1)
                        )
                if "settings" in msg:
                    hub.settings = LayoutSettings.from_dict(msg["settings"])
                hub.save()
                await hub.broadcast_state()

            elif msg_type == "update_settings":
                hub.settings = LayoutSettings.from_dict(msg.get("settings", {}))
                hub.save()
                await hub.broadcast_state()

            elif msg_type == "force_return_mac":
                for ws in list(hub.agent_sockets.values()):
                    try:
                        await ws.send_text(json.dumps({"type": "force_return_mac"}))
                    except Exception:
                        pass

            elif msg_type == "refresh_displays":
                for ws in list(hub.agent_sockets.values()):
                    try:
                        await ws.send_text(json.dumps({"type": "refresh_displays"}))
                    except Exception:
                        pass

    except WebSocketDisconnect:
        pass
    finally:
        hub.ui_sockets.discard(websocket)


# ---------------------------------------------------------------------------
# REST API Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/state")
async def api_get_state():
    return hub.to_cluster_payload()


@app.post("/api/layout")
async def api_save_layout(request: Request):
    body = await request.json()
    for d_raw in body.get("displays", []):
        did = d_raw.get("id")
        if did and did in hub.displays:
            disp = hub.displays[did]
            disp.canvas_x = round(float(d_raw.get("canvas_x", disp.canvas_x)), 1)
            disp.canvas_y = round(float(d_raw.get("canvas_y", disp.canvas_y)), 1)
            disp.canvas_width = max(
                200.0, round(float(d_raw.get("canvas_width", disp.canvas_width)), 1)
            )
            disp.canvas_height = max(
                150.0, round(float(d_raw.get("canvas_height", disp.canvas_height)), 1)
            )
    if "settings" in body:
        hub.settings = LayoutSettings.from_dict(body["settings"])
    hub.save()
    await hub.broadcast_state()
    return {"ok": True, "state": hub.to_cluster_payload()}


@app.post("/api/reset-sizes")
async def api_reset_display_sizes():
    """Reset all displays' canvas width/height back to their native 1:1 OS pixel dimensions."""
    for disp in hub.displays.values():
        disp.canvas_width = disp.os_width
        disp.canvas_height = disp.os_height
    hub.save()
    await hub.broadcast_state()
    return {"ok": True}


@app.delete("/api/device/{device_id}")
async def api_remove_device(device_id: str):
    hub.devices.pop(device_id, None)
    for did in [k for k, v in hub.displays.items() if v.device_id == device_id]:
        del hub.displays[did]
    hub.save()
    await hub.broadcast_state()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 1-Line Bootstrap Installers & Pre-Configured Client Bundle
# ---------------------------------------------------------------------------
def _build_server_urls(request: Request) -> Tuple[str, List[str]]:
    host_hdr = request.headers.get("host", f"{LAN_IP}:{HTTP_PORT}")
    req_host = host_hdr.split(":")[0]
    hosts = []
    for h in (req_host, LAN_IP, TAILSCALE_IP):
        if h and h not in hosts:
            hosts.append(h)
    ws_urls = [f"ws://{h}:{HTTP_PORT}/ws/agent" for h in hosts]
    base_http = f"http://{hosts[0]}:{HTTP_PORT}"
    return base_http, ws_urls


@app.get("/download/client.zip")
async def download_client_zip(request: Request):
    """Build a ready-to-run client.zip with embedded server URLs & pairing token."""
    _, ws_urls = _build_server_urls(request)
    config_data = {
        "server_urls": ws_urls,
        "token": hub.token,
        "udp_port": 8767,
        "server_udp_port": UDP_RELAY_PORT,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in (
            "multicontrol_agent.py",
            "mac_backend.py",
            "win_backend.py",
            "layout_engine.py",
            "protocol.py",
            "tray.py",
        ):
            fpath = CLIENT_DIR / fname
            if fpath.exists():
                zf.write(fpath, arcname=fname)

        zf.writestr("config.json", json.dumps(config_data, indent=2))

        run_mac = (
            "#!/usr/bin/env bash\n"
            'cd "$(dirname "$0")"\n'
            "if [ -x ./venv/bin/python ]; then\n"
            "  exec ./venv/bin/python multicontrol_agent.py\n"
            "else\n"
            "  exec python3 multicontrol_agent.py\n"
            "fi\n"
        )
        zf.writestr("run-mac.command", run_mac)

        run_win = (
            "@echo off\r\n"
            'cd /d "%~dp0"\r\n'
            "if exist venv\\Scripts\\pythonw.exe (\r\n"
            '  start /B "" venv\\Scripts\\pythonw.exe multicontrol_agent.py\r\n'
            ") else (\r\n"
            "  python multicontrol_agent.py\r\n"
            ")\r\n"
        )
        zf.writestr("run-windows.bat", run_win)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=multicontrol-client.zip"},
    )


@app.get("/install.sh", response_class=PlainTextResponse)
async def mac_install_script(request: Request):
    base_http, _ = _build_server_urls(request)
    script = f"""#!/usr/bin/env bash
set -e

echo "========================================================"
echo "  MultiControl macOS Agent Installer & Launcher"
echo "  Server Hub: {base_http}"
echo "========================================================"

INSTALL_DIR="$HOME/.multicontrol"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

echo "[1/4] Downloading pre-configured MultiControl client bundle..."
curl -fsSL "{base_http}/download/client.zip" -o client.zip
unzip -o -q client.zip
rm -f client.zip
chmod +x run-mac.command multicontrol_agent.py

echo "[2/4] Setting up Python virtual environment..."
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi

echo "[3/4] Installing macOS Quartz & Menu Bar dependencies..."
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet websockets pyobjc-framework-Quartz pystray Pillow

# Create convenience launcher on Desktop
cp -f "$INSTALL_DIR/run-mac.command" "$HOME/Desktop/MultiControl.command" 2>/dev/null || true
chmod +x "$HOME/Desktop/MultiControl.command" 2>/dev/null || true

echo "[4/4] Starting MultiControl macOS Agent..."
echo "      -> Look for the MultiControl dual-screen icon in your top Menu Bar!"
echo "      -> NOTE: If prompted by macOS, grant Accessibility permission to Terminal/Python."
echo "      -> Emergency Return to Mac Hotkey: Ctrl + Option + Esc"
exec "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/multicontrol_agent.py" < /dev/null
"""
    return script


@app.get("/install.ps1", response_class=PlainTextResponse)
async def windows_install_script(request: Request):
    base_http, _ = _build_server_urls(request)
    script = f"""$ErrorActionPreference = "Stop"
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  MultiControl Windows Agent Installer & Launcher" -ForegroundColor Cyan
Write-Host "  Server Hub: {base_http}" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

$InstallDir = Join-Path $env:USERPROFILE ".multicontrol"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Set-Location $InstallDir

Write-Host "[1/4] Downloading pre-configured MultiControl client bundle..." -ForegroundColor Yellow
Invoke-WebRequest -Uri "{base_http}/download/client.zip" -OutFile "client.zip" -UseBasicParsing
Expand-Archive -Path "client.zip" -DestinationPath $InstallDir -Force
Remove-Item "client.zip" -Force

Write-Host "[2/4] Setting up Python environment..." -ForegroundColor Yellow
if (-not (Test-Path "$InstallDir\\venv\\Scripts\\python.exe")) {{
    python -m venv "$InstallDir\\venv"
}}

Write-Host "[3/4] Installing dependencies (websockets, pystray, Pillow)..." -ForegroundColor Yellow
& "$InstallDir\\venv\\Scripts\\pip.exe" install --quiet --upgrade pip
& "$InstallDir\\venv\\Scripts\\pip.exe" install --quiet websockets pystray Pillow

# Open UDP 8767 in Windows Firewall for Direct P2P (<0.5ms latency)
try {{
    New-NetFirewallRule -DisplayName "MultiControl Direct UDP" -Direction Inbound -Protocol UDP -LocalPort 8767 -Action Allow -ErrorAction SilentlyContinue | Out-Null
}} catch {{ }}

# Create Desktop Shortcut
try {{
    $DesktopPath = [Environment]::GetFolderPath("Desktop")
    Copy-Item "$InstallDir\\run-windows.bat" (Join-Path $DesktopPath "MultiControl.bat") -Force
}} catch {{ }}

# Register or update Scheduled Task with Highest Privileges for Lock Screen & PIN entry
try {{
    $User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $Action = New-ScheduledTaskAction -Execute "$InstallDir\\venv\\Scripts\\pythonw.exe" -Argument "`"$InstallDir\\multicontrol_agent.py`"" -WorkingDirectory $InstallDir
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
    $Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Highest
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 0)

    Unregister-ScheduledTask -TaskName "MultiControlAgent" -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName "MultiControlAgent" -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description "MultiControl Agent with Highest Privileges for Lock Screen & PIN injection" | Out-Null
}} catch {{ }}

Write-Host "[4/4] Starting MultiControl Windows Agent (check your System Tray!)..." -ForegroundColor Green
Stop-Process -Name pythonw -Force -ErrorAction SilentlyContinue
try {{
    Start-ScheduledTask -TaskName "MultiControlAgent"
}} catch {{
    Start-Process "$InstallDir\\venv\\Scripts\\pythonw.exe" -ArgumentList "`"$InstallDir\\multicontrol_agent.py`"" -WorkingDirectory $InstallDir
}}
Write-Host "MultiControl is running! Lock screen PIN entry is now supported." -ForegroundColor Cyan
"""
    return script


@app.get("/install-service.ps1", response_class=PlainTextResponse)
async def windows_service_install_script(request: Request):
    """Installs MultiControl as a boot-time background service running as SYSTEM for cold-boot login."""
    base_http, _ = _build_server_urls(request)
    script = f"""$ErrorActionPreference = "Stop"
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  MultiControl Windows Boot Service Installer (SYSTEM)" -ForegroundColor Cyan
Write-Host "  Enables Lock Screen, PIN Entry & Boot-Time Remote Control" -ForegroundColor Cyan
Write-Host "  Server Hub: {base_http}" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {{
    Write-Host "[!] Administrator privileges are required to install Service Mode." -ForegroundColor Red
    Write-Host "[!] Please open PowerShell as Administrator and run:" -ForegroundColor Yellow
    Write-Host "    irm {base_http}/install-service.ps1 | iex" -ForegroundColor White
    exit 1
}}

$InstallDir = Join-Path $env:ProgramData "MultiControl"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Set-Location $InstallDir

Write-Host "[1/4] Downloading MultiControl Service bundle..." -ForegroundColor Yellow
Invoke-WebRequest -Uri "{base_http}/download/client.zip" -OutFile "client.zip" -UseBasicParsing
Expand-Archive -Path "client.zip" -DestinationPath $InstallDir -Force
Remove-Item "client.zip" -Force

Write-Host "[2/4] Setting up Python environment..." -ForegroundColor Yellow
if (-not (Test-Path "$InstallDir\\venv\\Scripts\\python.exe")) {{
    python -m venv "$InstallDir\\venv"
}}

Write-Host "[3/4] Installing dependencies..." -ForegroundColor Yellow
& "$InstallDir\\venv\\Scripts\\pip.exe" install --quiet --upgrade pip
& "$InstallDir\\venv\\Scripts\\pip.exe" install --quiet websockets pystray Pillow

try {{
    New-NetFirewallRule -DisplayName "MultiControl Direct UDP" -Direction Inbound -Protocol UDP -LocalPort 8767 -Action Allow -ErrorAction SilentlyContinue | Out-Null
}} catch {{ }}

Write-Host "[4/4] Registering MultiControl Boot Service (Runs as SYSTEM on Boot)..." -ForegroundColor Yellow

$Action = New-ScheduledTaskAction -Execute "$InstallDir\\venv\\Scripts\\pythonw.exe" -Argument "`"$InstallDir\\multicontrol_agent.py`" --no-tray" -WorkingDirectory $InstallDir
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\\SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Unregister-ScheduledTask -TaskName "MultiControlService" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "MultiControlService" -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description "MultiControl Boot-Time Receiver Service for Lock Screen and Cold Boot input control" | Out-Null

Stop-Process -Name pythonw -Force -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName "MultiControlService"

Write-Host "========================================================" -ForegroundColor Green
Write-Host "  MultiControl Boot Service installed & running as SYSTEM!" -ForegroundColor Green
Write-Host "  You can now control Windows on the Lock/PIN Screen and on Cold Boot." -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
"""
    return script


# ---------------------------------------------------------------------------
# Static Web UI
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index_page():
    return FileResponse(str(STATIC_DIR / "index.html"))
