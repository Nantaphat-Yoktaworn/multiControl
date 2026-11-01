# MultiControl — Seamless Mac Keyboard & Mouse Control for Windows PC

**MultiControl** lets you use your **Mac keyboard and mouse/trackpad** to control your **Windows PC** across the network with pixel-perfect cursor transitions.

It consists of:
1. **Central Server Hub & 2D Screen Layout Studio** (running in Docker on this Linux server `linux-server` at `192.168.1.100:8765` and Tailscale `100.64.0.10:8765`).
2. **macOS Master Agent** (`Quartz` / `CoreGraphics` `CGEventTap` + Menu Bar icon) — captures global mouse, smooth trackpad scrolling, and keyboard events, locks/hides the Mac cursor while on Windows, and restores it at the exact entry coordinate when crossing back.
3. **Windows Receiver Agent** (`Win32 ctypes SendInput` + Per-Monitor DPI Awareness V2 + System Tray icon) — injects ultra-low-latency cursor movement, clicks, horizontal/vertical scroll, and scan-code keyboard events.
4. **Direct Peer-to-Peer UDP Fast-Path (`<0.5ms` on LAN)** with automatic fallback to the Server UDP Relay (`8766/udp`) if Windows Firewall blocks direct peer UDP.

---

## 🚀 Quick Start

### 1. Start the Central Hub on This Server (Docker)

```bash
cd /path/to/multiControl
./scripts/start-docker.sh
# or directly:
docker compose up -d --build
```

Open the **2D Screen Layout Studio Web UI** from any browser:
- **Local LAN:** `http://192.168.1.100:8765`
- **Tailscale VPN:** `http://100.64.0.10:8765`

---

### 2. Connect Your Mac (Master Keyboard & Mouse)

Run this single command in **macOS Terminal** (or copy it from the **⚡ Connect Mac & Windows** button in the Web UI):

```bash
curl -fsSL http://192.168.1.100:8765/install.sh | bash
```
*(If connecting over Tailscale, use `http://100.64.0.10:8765/install.sh`)*

- Installs the agent into `~/.multicontrol` and creates a `MultiControl.command` shortcut on your Desktop.
- Places the **MultiControl** icon in your macOS Menu Bar.
- **Important:** When macOS prompts you, grant **Accessibility** permission to **Terminal / Python** in **System Settings → Privacy & Security → Accessibility** so `CGEventTap` can capture & lock the cursor when crossing to Windows.

---

### 3. Connect Your Windows PC (Target)

Run this single command in **Windows PowerShell**:

```powershell
irm http://192.168.1.100:8765/install.ps1 | iex
```
*(If connecting over Tailscale, use `http://100.64.0.10:8765/install.ps1`)*

- Installs the agent into `%USERPROFILE%\.multicontrol` and creates a `MultiControl.bat` launcher on your Desktop.
- Places the **MultiControl** icon in your Windows System Tray.

---

## 🖥️ Arranging Your Screens & Calibrating Physical Size

In the Web UI (`http://192.168.1.100:8765`):
1. **Drag & Snap Monitors:** Drag your Mac and Windows displays freely on the 2D canvas. Magnetic snap guides automatically align edges or centers.
2. **Physical Size Calibration:** If your MacBook screen is physically smaller on your desk than your external Windows monitor, click the display and drag its **bottom-right resize handle** (or adjust the **Physical Monitor Scale %** slider in the left sidebar). The transition engine maps the cursor proportionally so crossing at any physical height on your desk lands at the exact matching height on the other screen.
3. **Test Cursor Crossing:** Click **🎯 Test Cursor Crossing** in the top bar and move your cursor inside the Mac display on the canvas to preview the exact entry coordinates `(x, y)` on the Windows display.

---

## ⌨️ Built-in Shortcuts & Features

| Shortcut / Feature | Description |
| :--- | :--- |
| **`Ctrl + Option + Esc`** | **Emergency Return to Mac:** Immediately unlocks the Mac cursor and brings control back to your Mac |
| **Swap Mac `⌘ Cmd` ↔ Windows `Ctrl`** | Enabled by default (toggleable in Web UI) so `⌘C`, `⌘V`, `⌘Z`, `⌘A`, `⌘F` work naturally on Windows |
| **Shared Clipboard Sync** | Automatically syncs plain-text clipboard whenever the cursor crosses between Mac and Windows |
