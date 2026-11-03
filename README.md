# MultiControl — Seamless Mac Keyboard, Mouse & Trackpad Control for Windows PC

**MultiControl** lets you use your **Mac keyboard, mouse, and Multi-Touch trackpad** to control your **Windows PC** across the network with sub-millisecond latency and pixel-perfect cursor transitions.

---

## 🏗️ Architecture

1. **Central Server Hub & 2D Screen Layout Studio (`server/main.py`)**
   - Containerized FastAPI + WebSocket Hub (`8765/tcp`) and zero-copy UDP Relay (`8766/udp`).
   - Runs in Docker on `linux-server` (`192.168.1.100:8765` on LAN / `100.64.0.10:8765` on Tailscale) and auto-starts on boot via `systemctl --user enable multicontrol.service`.
   - **Active Bidirectional Heartbeat & Stale Socket Protection:** Periodically pings connected agents every 10 seconds to detect silent network drops immediately. Automatically supersedes older socket connections upon reconnects so stale sockets never mark active devices offline.
2. **macOS Master Agent (`client/mac_backend.py` & `client/multicontrol_agent.py`)**
   - Captures global mouse deltas, buttons, smooth trackpad scrolling, hardware volume/media keys (`NSEventTypeSystemDefined`), and keyboard events via `Quartz` (`CGEventTap` at `kCGHIDEventTap` and `kCGSessionEventTap`).
   - Hooks Apple's `MultitouchSupport.framework` for native MacBook trackpad 3-finger middle-click and 3-finger vertical swipe gestures (`Win + Tab` / `Esc`) on Windows.
   - Uses CoreGraphics `SetsCursorInBackground` + `CGSHideCursor` to seamlessly hide and lock the local Mac cursor while controlling Windows, and restores it at the exact entry coordinate when crossing back.
3. **Windows Receiver Agent (`client/win_backend.py` & `client/multicontrol_agent.py`)**
   - Uses 64-bit Win32 `ctypes` `SendInput` + Per-Monitor DPI Awareness V2 (`DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2`) for hardware-level cursor positioning, clicks, smooth pixel/wheel scrolling, and scan-code + Virtual-Key keyboard injection.
   - Parks the Windows cursor at the right-edge vertical midpoint when returning to Mac so it never triggers the Windows 11 taskbar corner hover.
   - **System Integrity & UIPI Bypass (`client/service_watchdog.py`):** Runs under `NT AUTHORITY\SYSTEM` in Session 0. Uses `CreateProcessAsUserW` with a duplicated `winlogon.exe` primary token to spawn the agent into the interactive console session (`Session 2`, etc.) with **System Integrity**, allowing input injection to bypass Windows UIPI restrictions on the **Lock Screen / PIN Prompt (`LogonUI.exe`)**, UAC dialogs, and cold-boot screens.
   - **Robust Reconnection & Tray Sync:** Features connection timeouts, direct LAN prioritization over VPN, thread-safe Win32 tray state sync, and instant visual reconnection feedback (`● Reconnecting to Hub...`).
   - **Secure Lock Workstation Interceptor:** Automatically detects `Win + L` (sent as Mac `Control + L` with default modifier swapping) and calls `user32.LockWorkStation()` directly, bypassing Windows security restrictions that block synthetic `SendInput(Win+L)`.
   - **Authentic Hardware Scancodes:** Maps keys using `MapVirtualKeyW` and sends `KEYEVENTF_SCANCODE` (`0x0008`) so Windows Credential Provider reliably accepts keystrokes in secure PIN and password fields.
4. **Hybrid P2P UDP + Reliable Dual-Path Transport (`client/protocol.py`)**
   - Streams high-frequency input packets directly peer-to-peer over UDP (`<0.5ms` on LAN) with automatic fallback to the Server UDP Relay (`8766/udp`).
   - Uses a 512-entry rolling sequence deduplication window and dual-path reliable delivery for state transitions (`ENTER_SCREEN`, `LEAVE_SCREEN`, `MOUSE_BUTTON`, `KEY_EVENT`) so clicks and keys are never dropped, duplicated, or stuck.

---

## 🚀 Quick Start

### 1. Start the Central Hub on the Server (Docker)

```bash
cd /path/to/multiControl
./scripts/start-docker.sh
# or directly:
docker compose up -d --build
```

Open the **2D Screen Layout Studio Web UI** in any browser:
- **Local LAN:** `http://192.168.1.100:8765`
- **Tailscale VPN:** `http://100.64.0.10:8765`

---

### 2. Install & Run on Your Mac (Master Keyboard & Trackpad)

Run this 1-line installer in **macOS Terminal** (or copy it from **⚡ Connect Mac & Windows** in the Web UI):

```bash
curl -fsSL http://192.168.1.100:8765/install.sh | bash
```
*(Over Tailscale: `curl -fsSL http://100.64.0.10:8765/install.sh | bash`)*

- Installs the agent into `~/.multicontrol`, places the **MultiControl** icon in your macOS Menu Bar, and configures auto-start on login.
- Grant **Accessibility** permission in **System Settings → Privacy & Security → Accessibility** when prompted.

---

### 3. Install & Run on Your Windows PC (Target Receiver)

Run this 1-line installer in **Windows PowerShell** (Run as Administrator recommended for Lock/PIN screen injection):

```powershell
irm http://192.168.1.100:8765/install.ps1 | iex
```
*(Over Tailscale: `irm http://100.64.0.10:8765/install.ps1 | iex`)*

- Installs the agent into `%USERPROFILE%\.multicontrol`, places the **MultiControl** icon in your Windows System Tray, and configures auto-start with Highest Privileges so mouse and keyboard control work seamlessly on the **Windows Lock Screen & PIN/Password entry screen** (`Winlogon`).

#### 🛡️ Recommended: Boot Service Mode (Lock Screen, PIN Entry & Cold Boot Remote Control)
To control Windows on the Lock Screen / PIN entry prompt (`LogonUI.exe`) and after a fresh reboot/cold boot *before* any user logs in, run:
```powershell
irm http://192.168.1.100:8765/install-service.ps1 | iex
```
*(Over Tailscale: `irm http://100.64.0.10:8765/install-service.ps1 | iex`)*

This registers `MultiControlService` as a boot-time background service running under `NT AUTHORITY\SYSTEM`. The service watchdog uses `CreateProcessAsUser` to spawn the session worker inside the active console session with System Integrity, completely bypassing Windows UIPI limitations.

---

## ⌨️ Shortcuts, Trackpad Gestures & Media Keys

| Input / Gesture | Behavior When Cursor is on **Windows** | Behavior When Cursor is on **Mac** |
| :--- | :--- | :--- |
| **`Ctrl + Option + Esc`** | **Emergency Return to Mac:** Immediately unlocks the Mac cursor and brings control back to Mac | — |
| **Mac `Control + L`** *(or `Win + L`)* | **Lock Windows Workstation:** Directly calls `user32.LockWorkStation()` to lock Windows (since Windows blocks synthetic software `SendInput` on Win+L) | Native macOS behavior |
| **Mac `Control + D`** *(or `Win + D`)* | **Show Desktop:** Minimizes/restores all windows | Native macOS behavior |
| **Mac `Control + P`** *(or `Win + P`)* | **Display Project Settings:** Opens Windows display projection panel | Native macOS behavior |
| **3-Finger Click or Tap** *(Trackpad)* | **Middle Mouse Click (`Button 3`):** Supports both middle-click (close/open browser tabs) and 3-finger click-and-hold drag (panning in CAD/3D apps); suppresses macOS Look Up popup | Normal macOS 3-finger behavior |
| **3-Finger Swipe Up** *(Trackpad)* | Opens **Windows Task View (`Win + Tab`)** and suppresses macOS Mission Control | Opens **macOS Mission Control** |
| **3-Finger Swipe Down** *(Trackpad)* | Presses **`Esc`** on Windows to dismiss Task View | Normal macOS App Exposé / close Mission Control |
| **Volume Up / Down / Mute** *(F10–F12)* | Controls **Windows PC Volume & Mute** (local Mac volume is unchanged) | Controls **Mac Volume & Mute** |
| **Play / Pause / Next / Previous** | Controls **Windows Media Playback** | Controls **Mac Media Playback** |
| **Swap Mac `⌘ Cmd` ↔ Windows `Ctrl`** | Enabled by default (toggleable in Web UI) so `⌘C`, `⌘V`, `⌘Z`, `⌘A`, `⌘F` work naturally on Windows; Mac `Control` maps to Windows Key (`Win`) | Native macOS `⌘ Cmd` |
| **Shared Clipboard Sync** | Automatically syncs plain-text clipboard in the background whenever the cursor crosses between Mac and Windows | Syncs back to Mac on return |

---

## 🖥️ Arranging Your Screens & Calibrating Physical Size

In the Web UI (`http://192.168.1.100:8765`):
1. **Drag & Snap Monitors (Horizontal or Vertical):** Drag your Mac and Windows displays freely on the 2D canvas (side-by-side or stacked vertically, e.g. MacBook below an ultrawide Windows monitor). Magnetic snap guides automatically align edges or centers.
2. **Physical Size Calibration:** Click any display on the canvas and drag its **bottom-right resize handle** (or use the **Physical Monitor Scale %** slider in the left sidebar) so its width on the canvas matches its real-world physical width on your desk. Cursor crossings map proportionally across shared edges.
3. **Drag Protection:** Dragging a window or selecting text to the edge of a screen clamps to the active monitor until the mouse button is released, preventing accidental screen switches mid-drag.
4. **Test Cursor Crossing:** Click **🎯 Test Cursor Crossing** in the top bar and move your cursor inside the Mac display on the canvas to preview the exact entry coordinates `(x, y)` on the Windows display.

---

## 🔄 Auto-Start on Boot & Service Management

All three machines are configured to start MultiControl automatically on boot/login:

- **Linux Server (`linux-server`):**
  ```bash
  systemctl --user status multicontrol.service
  docker compose ps
  ```
- **macOS (`mac-host`):**
  - LaunchAgent: `~/Library/LaunchAgents/com.multicontrol.agent.plist`
  - Restart manually:
    ```bash
    launchctl kickstart -k gui/$(id -u)/com.multicontrol.agent
    ```
- **Windows PC (`windows-pc`):**
  - **Boot Service Watchdog (`MultiControlService` - Recommended):** Scheduled Task running at startup under `NT AUTHORITY\SYSTEM` in Session 0. Automatically manages the interactive console session worker with System Integrity for Lock/PIN screens, UAC, and cold boot.
    ```powershell
    # Restart the watchdog service and its interactive session worker:
    Stop-ScheduledTask -TaskName MultiControlService; Stop-Process -Name pythonw -Force -ErrorAction SilentlyContinue; Start-ScheduledTask -TaskName MultiControlService
    # View watchdog status and session worker spawn logs:
    Get-Content C:\ProgramData\MultiControl\watchdog.log -Tail 20
    Get-Content C:\ProgramData\MultiControl\agent.log -Tail 20
    ```
  - **User-Level Agent (`MultiControlAgent`):** Scheduled Task running at user logon under the interactive user account. (Disabled when Service Mode is used to prevent port conflicts).

---

## 🧪 Running Unit Tests

```bash
python3 -m unittest discover -s tests -v
```
