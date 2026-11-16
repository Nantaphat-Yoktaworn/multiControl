# MultiControl v2 — Serverless Peer-to-Peer Two-Machine Architecture & Implementation Plan

> **Branch:** `feat/p2p-two-machine`
> **Status:** Research & Architecture Plan (no source code modified yet)
> **Scope:** Remove the central server hub (`server/main.py`, Docker container, systemd hub), convert MultiControl into a direct LAN two-machine desktop application where users click **Make Master** in the local dashboard to choose which machine controls the other, add end-to-end encryption, support all Mac & Windows combinations across Intel/Apple Silicon/x64/ARM64, and package standalone installers for general end users.

---

## Table of Contents

1. [Executive Summary & Locked Design Decisions](#1-executive-summary--locked-design-decisions)
2. [Current Codebase Audit & Gap Analysis](#2-current-codebase-audit--gap-analysis)
3. [Target Serverless Two-Machine Architecture](#3-target-serverless-two-machine-architecture)
4. [LAN Discovery, Pairing & End-to-End Encryption](#4-lan-discovery-pairing--end-to-end-encryption)
5. [Canonical Wire Protocol & Bidirectional Key Mapping](#5-canonical-wire-protocol--bidirectional-key-mapping)
6. [macOS Bidirectional Backend (`mac_backend.py`)](#6-macos-bidirectional-backend-mac_backendpy)
7. [Windows Bidirectional Backend (`win_backend.py` & Privilege Split)](#7-windows-bidirectional-backend-win_backendpy--privilege-split)
8. [Embedded Local Dashboard & Master/Slave Switching](#8-embedded-local-dashboard--masterslave-switching)
9. [Packaging, End-User Installers & CI/CD Pipeline](#9-packaging-end-user-installers--cicd-pipeline)
10. [Repository Cleanup & Migration Map](#10-repository-cleanup--migration-map)
11. [Phased Implementation Roadmap & Verification Matrix](#11-phased-implementation-roadmap--verification-matrix)

---

## 1. Executive Summary & Locked Design Decisions

Every design branch below has been aligned and locked before implementation:

| Design Area | Chosen Architecture |
| :--- | :--- |
| **Topology** | **Two-machine direct P2P only** — central server (`server/main.py`, Docker hub, UDP relay `8766`) completely removed. |
| **Role Selection** | **Click-to-Claim Master on Dashboard** — user opens the dashboard on either machine and clicks **Make Master**; that machine becomes Master (Controller) and the paired peer becomes Slave (Receiver). |
| **OS & Architecture Matrix** | **All 4 pairs supported** (`Mac→Win`, `Win→Mac`, `Mac→Mac`, `Win→Win`) across **macOS 12+ Universal2 (`arm64` + `x86_64`)** and **Windows 10/11 (`x64` + `ARM64`)**. |
| **Keyboard Mapping Defaults** | Smart per-pair defaults: auto-swap `Ctrl ↔ ⌘ Cmd` when crossing OS (`Mac↔Win`), 1:1 unswapped for same-OS (`Mac↔Mac`, `Win↔Win`), all toggleable in the dashboard. |
| **Dashboard Delivery** | **Embedded local Web Dashboard** (`http://127.0.0.1:8765`) served inside each desktop agent and opened in the system default browser via Menu Bar / System Tray (reusing and upgrading the existing 2D Canvas Studio). |
| **Discovery & Pairing** | **mDNS (`_multicontrol._tcp.local.`) + UDP LAN Broadcast fallback + Manual IP/Tailscale fallback**, paired once via **1-click Connect + 6-digit PIN verification**, then remembered for zero-prompt auto-reconnect. |
| **Transport Security** | **Always-on encryption**: TLS (`wss://`) with pinned peer certificate fingerprint on the control plane + **ChaCha20-Poly1305** authenticated encryption on UDP input packets (`<0.05 ms` overhead). |
| **Windows Privileges** | **Default User-Mode install** (works without Admin UAC) + **optional "Lock Screen & Elevated/UAC Control" toggle** using a split `SYSTEM` input helper via ACL-protected IPC so the tray and browser never run as `NT AUTHORITY\SYSTEM`. |
| **Distribution & Packaging** | Keep Python core packaged as standalone installers (**macOS Universal2 `.dmg` / `.app`** + **Windows `x64` & `ARM64` Inno Setup `.exe`**) requiring **zero pre-installed Python** on user machines. |
| **CI/CD & Signing** | Automated GitHub Actions builds (`macos-latest` Universal2 + `windows-latest` x64 + `windows-11-arm` ARM64) with optional Apple Notarization / Windows Authenticode signing hooks, clear unsigned bypass instructions, and a lightweight GitHub Releases update check. |

---

## 2. Current Codebase Audit & Gap Analysis

### 2.1 Existing Files & Roles

| File | Current Role | Disposition in v2 |
| :--- | :--- | :--- |
| `server/main.py` (880 lines) | FastAPI Hub (`8765/tcp`), UDP Relay (`8766/udp`), hardcoded `192.168.1.100` / `100.64.0.10`, 1-line `install.sh` / `install.ps1` / `install-service.ps1` / `client.zip` generator | **Remove central hub & remote installers.** Extract local Web UI serving & REST/WS state logic into an embedded per-agent local web module (`multicontrol/web_ui.py`). |
| `Dockerfile`, `docker-compose.yml`, `scripts/multicontrol.service`, `scripts/start-docker.sh`, `scripts/run-local.sh` | Dockerized central server deployment on Linux host `linux-server` | **Delete** (app is now pure desktop P2P running natively on Mac & Windows; desktop HID capture/injection cannot run inside Linux containers). |
| `server/static/index.html`, `app.js`, `styles.css` | 2D Screen Layout Studio Web UI (`canvas` drag/resize, presets, settings, test cursor crossing, Connect modal) | **Keep & Upgrade** into `multicontrol/ui/` — remove Central Hub Connect modal & hardcoded IPs; add **LAN Discovery & Pairing**, **Make Master / Role Banner**, and **OS-aware Input Settings**. |
| `client/layout_engine.py` (506 lines) | `DisplayRect`, `LayoutSettings`, `VirtualCanvasEngine` (`step_from_native`, `step_virtual_delta`, edge portals, corner barrier) | **Keep & Extend** — generalize `mouse_speed_windows` / `invert_scroll_on_windows` / `three_finger_swipe_windows` to support both directions (`slave_mouse_speed`, `invert_scroll_remote`, per-direction modifier swap). |
| `client/protocol.py` (350 lines) | Binary UDP framing (`MC` v1, 16-byte header + 8B token SHA-256 digest) + one-way `MAC_KEYCODE_TO_WIN_VK` | **Upgrade to v2** — canonical USB HID keycodes (`CanonicalKey`), bidirectional translation (`Mac ↔ HID ↔ Win`), and ChaCha20-Poly1305 AEAD framing. |
| `client/mac_backend.py` (1051 lines) | macOS **Capture only** (`CGEventTap`, `MultitouchSupport.framework`, `CGSHideCursor`, `CGAssociateMouseAndMouseCursorPosition`) + display enum + `pbpaste`/`pbcopy` | **Extend** — add `MacInputInjector` (`CGEventCreateMouseEvent`, `CGEventCreateKeyboardEvent`, `CGEventCreateScrollWheelEvent2`, `NX_SYSDEFINED` media injection), guard `MultitouchSupport` for trackpad-less Macs, and add TCC permission checks. |
| `client/win_backend.py` (962 lines) | Windows **Injection only** (`SendInput`, `SetCursorPos`, `OpenInputDesktop` / `winlogon.exe` impersonation, `LockWorkStation`) + display enum + Win32 clipboard | **Extend** — add `WinInputController` (`WH_MOUSE_LL` + `WH_KEYBOARD_LL` + Raw Input `WM_INPUT` + crash-safe invisible cursor trap window), plus IPC client/server split for optional `SYSTEM` helper. |
| `client/service_watchdog.py` (286 lines) | Session-0 `SYSTEM` watchdog spawning `venv\Scripts\pythonw.exe launch_logged.py` into active console session | **Refactor** — support frozen PyInstaller executable (`MultiControl.exe --helper`), run **only** as an optional privileged input helper over ACL-protected Named Pipe instead of running the whole UI/browser as `SYSTEM`. |
| `client/multicontrol_agent.py` (1114 lines) | Central-hub client assuming `darwin == master` and `windows == slave`; hardcodes fallback hub IPs | **Refactor** into `multicontrol/agent.py` — symmetric P2P state machine (`role: "master" | "slave" | "idle"`), dynamic backend activation, replicated layout state, TLS WS + encrypted UDP transport. |
| `client/tray.py` (256 lines) | `pystray` Menu Bar / System Tray icon (hardcoded `"Connecting to Server..."`, `"Return Cursor to Mac"`) | **Upgrade** — role-aware status (`Master` / `Slave` / `Unpaired`), quick **"Make This Machine Master"** menu item, **"Open Dashboard"**, OS-agnostic return shortcut label. |
| `data/state.json` | Contains personal hostnames/IPs/tokens (`linux-server`, `mac-host`, `windows-pc`, `mc-<token>`) | **Remove from git tracking** (add `data/state.json` to `.gitignore`); store per-user runtime config in OS-standard AppData directories. |

---

## 3. Target Serverless Two-Machine Architecture

### 3.1 Symmetry Principle: Every Install Is Identical

Both machines run the exact same **MultiControl** desktop application. Neither machine is hardcoded as server or client:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Machine A (Mac or Win)                         │
│                                                                         │
│  [Tray / Menu Bar] (Main Thread: AppKit NSStatusItem / Win32 ShellTray) │
│         │                                                               │
│  [Local Dashboard Server] http://127.0.0.1:8765 (Browser Web UI)        │
│         │                                                               │
│  [Peer Session Coordinator] ◄──── TLS WebSocket (:8768) ────► Peer B    │
│    • Discovery (mDNS + UDP Beacon :8769)                                │
│    • Pairing (6-digit PIN + Cert Pinning)                               │
│    • State Sync (Revisioned Layout + Settings + Role)                   │
│    • Clipboard Sync & Role Handoff (CLAIM_MASTER)                       │
│         │                                                               │
│  [Input Engine]                                                         │
│    • If role == "master":  Capture Backend ──► VirtualCanvasEngine      │
│                            ──► Encrypted UDP (:8767) ───────► Peer B    │
│    • If role == "slave":   Encrypted UDP (:8767) ◄─────────── Peer B    │
│                            ──► Injection Backend (or Win SYSTEM Helper) │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Threading & Concurrency Model

Because macOS AppKit requires UI/Tray callbacks on the **main OS thread**, and low-level hooks (`CGEventTap` / `WH_MOUSE_LL`) require tight real-time loops:

1. **Thread 1 — Main Thread (`TrayController`):**
   - macOS: `AppKit` / `pystray` `NSStatusItem` run loop (`AppHelper.callAfter` for UI updates).
   - Windows: Win32 tray message loop (`Shell_NotifyIconW`).
2. **Thread 2 — Asyncio Runtime Thread (`AsyncRuntime`):**
   - Runs one `asyncio` event loop hosting:
     - **Local Web Dashboard** (`127.0.0.1:8765`, Starlette + Uvicorn with `install_signal_handlers` overridden to no-op so Uvicorn runs cleanly off the main thread).
     - **P2P TLS Control Server** (`0.0.0.0:8768`, `websockets` TLS server accepting the paired peer's control connection) **and/or** **P2P TLS Control Client** (auto-connecting to the paired peer when acting as connector — deterministic tie-breaker by `device_id` lexicographical comparison so both peers never clash!).
     - **Discovery Service** (`zeroconf` `AsyncZeroconf` + UDP broadcast listener/announcer on `8769/udp`).
3. **Thread 3 — Real-Time UDP Input Thread (`UDPRxLoop`):**
   - Non-blocking UDP socket (`0.0.0.0:8767`) with `select` + ChaCha20-Poly1305 AEAD verification + 512-entry rolling deduplication ring buffer.
   - When `role == "slave"`, dispatches directly to the local Injector (`MacInputInjector` or `WinInputInjector` / Named Pipe helper) with zero `asyncio` loop hop.
4. **Thread 4 — Native Capture Thread (`MacInputController` / `WinInputController`):**
   - Activated **only** when `role == "master"` (and cleanly paused/unhooked when `role == "slave"` or `"idle"`).
   - Sends UDP packets directly from the capture callback / fast lock-free queue without blocking the OS hook.

### 3.3 Deterministic Connection & Role Ownership

To eliminate central-server coordination without split-brain bugs:

- **Transport Tie-Breaker (Who Connects to Whom on TLS :8768):**
  - Both machines listen on TLS port `8768` AND know each other's endpoints from mDNS / UDP beacon / saved peer record.
  - Whichever machine has the **lexicographically smaller `device_id`** initiates the outbound TLS WebSocket connection to the peer (with fast fallback: if one machine is behind a strict firewall and can't accept inbound TCP `8768`, the peer initiates in reverse after 2 seconds!). Any duplicate connection is deduplicated by `peer_device_id`.
- **Two-Machine Enforced Cap:**
  - Each installation maintains a `paired_peer` slot (strictly **two-machine** scope).
  - Pairing with a new machine prompts to disconnect/replace the currently paired machine.
- **Role Handoff State Machine (`master` ↔ `slave`):**
  - State includes `master_device_id`, `role_epoch: int` (monotonically incrementing), and `state_rev: int` (Lamport revision counter).
  - When user clicks **"Make This Machine Master"** on Machine B's dashboard (even while Machine A is currently Master):
    1. Machine B increments `role_epoch` and sends `{ "type": "claim_master", "device_id": B, "role_epoch": N+1 }` over TLS WS.
    2. Machine A immediately executes `force_return_local()`: unlocks/shows its own cursor, clears held modifiers/buttons, stops/disarms capture, switches to `role = "slave"`, sends `LEAVE_SCREEN` cleanup, and replies `{ "type": "master_ack", "role_epoch": N+1 }`.
    3. Machine B enables its Capture Controller (`role = "master"`), places the active virtual cursor at the center of Machine B's primary display, and broadcasts updated `cluster_state` to both dashboards.
  - **Fail-safe:** If the Slave disconnects unexpectedly while Master's cursor is on the Slave screen, Master detects TLS disconnect / UDP silence and immediately triggers `force_return_local()` so the user is never stranded with a hidden/locked cursor.

### 3.4 Layout & Settings Replication

- Both machines store `state.json` locally in OS-standard user data directories:
  - **macOS:** `~/Library/Application Support/MultiControl/state.json`
  - **Windows:** `%LOCALAPPDATA%\MultiControl\state.json`
  - *(Legacy dev CLI override `--data-dir` preserved for testing).*
- Every edit in either dashboard (dragging displays, scaling, toggling `swap_cmd_ctrl`, scroll speed, etc.) increments `state_rev`, persists locally, and pushes `sync_state` over the TLS WebSocket to the peer, which applies and saves it immediately.
- On reconnect, the higher `(state_rev, updated_at)` wins automatically; each machine refreshes its live local `DisplayRect[]` (`os_x`, `os_y`, `os_width`, `os_height`, `scale_factor`) while preserving custom `canvas_x/y/width/height`.

---

## 4. LAN Discovery, Pairing & End-to-End Encryption

### 4.1 Multi-Layer LAN Discovery (Zero Configuration)

| Layer | Mechanism | Purpose |
| :--- | :--- | :--- |
| **1. mDNS / DNS-SD** | `zeroconf` (`_multicontrol._tcp.local.`) | Standard zero-config discovery on macOS Bonjour & Windows LANs. TXT record publishes `id`, `name`, `os`, `arch`, `ver`, `tcp_port`, `udp_port`, `fp` (truncated cert fingerprint). |
| **2. UDP Subnet Broadcast** | Broadcast JSON beacon on `8769/udp` every `2.0s` while discovery UI is open (and on startup) | Handles home/office routers or Windows hosts where mDNS UDP `5353` is filtered or delayed. |
| **3. Manual IP / Hostname** | User enters `192.168.1.42` (or `100.x.x.x` Tailscale IP) in Dashboard | Handles AP-isolated Wi-Fi, multi-VLAN setups, and direct Tailscale/WireGuard peers with zero central server. |

> **macOS 15 Sequoia+ Local Network Privacy Requirement:**
> macOS 15+ silently drops local sockets/multicast for `.app` bundles unless `Info.plist` explicitly declares `NSLocalNetworkUsageDescription` and `NSBonjourServices` (`_multicontrol._tcp`). See [Section 6.4](#64-macos-permissions-sequoia-local-network-privacy--constraints).

### 4.2 First-Time Pairing Flow (6-Digit PIN + Mutual Cert Pinning)

1. On first launch, each app generates an **ECDSA P-256** (or RSA-2048) self-signed identity keypair + certificate (`identity.crt`, `identity.key`) and computes its `cert_sha256` fingerprint.
2. User opens Dashboard on Machine A → sees Machine B in **Nearby Machines** → clicks **Pair**.
3. Machine A opens a temporary TLS WebSocket to Machine B (`8768/tcp`, accepting provisional cert during pairing only) and sends `pair_request` with Machine A's name, OS, and `cert_sha256`.
4. Machine B generates a fresh random **6-digit PIN** (valid for 120 seconds, rate-limited to 5 failed attempts) and pops a banner in Machine B's Dashboard (plus OS tray notification):
   > *"MacBook Pro wants to connect — Enter PIN **482 915** on MacBook Pro to pair."*
5. User types `482915` on Machine A's Dashboard.
6. **Zero-Plaintext-PIN Verification (Mutual Challenge-Response):**
   - Both sides compute `auth_key = PBKDF2_HMAC_SHA256(pin, salt = min_id || max_id || nonce_a || nonce_b, iterations = 100_000)`.
   - Machine A sends `HMAC(auth_key, "A:" || cert_fp_a || cert_fp_b)`; Machine B verifies and returns `HMAC(auth_key, "B:" || cert_fp_b || cert_fp_a)` plus an encrypted `udp_master_secret` (32 random bytes, or derived via ECDH P-256 ephemeral key exchange authenticated by `auth_key`!).
   - Both sides persist `{ peer_device_id, peer_name, peer_os, peer_cert_sha256, peer_ips, udp_master_secret }` into `trusted_peers.json`.
7. **Future Reconnects:** Automatic mutual TLS fingerprint check + fresh per-session UDP key derived via `HKDF-SHA256(udp_master_secret, salt = nonce_master || nonce_slave, info = "mc-v2-udp")`. Zero user prompts required.

### 4.3 Transport Encryption & Latency Budget

- **Control Plane (`8768/tcp`):** `wss://` (TLS 1.2/1.3) pinning `peer_cert_sha256`. Carries pairing, state/layout sync, clipboard text, `CLAIM_MASTER`, and reliable fallback input events.
- **Real-Time Input Stream (`8767/udp`):**
  - Authenticated Encryption with Associated Data (**ChaCha20-Poly1305**) using `pycryptodome` (`Crypto.Cipher.ChaCha20_Poly1305`) — chosen because PyPI publishes prebuilt binary wheels for **`win_arm64`**, **`win_amd64`**, and **`macosx_universal2`** without requiring a Rust compiler (unlike `cryptography` which lacks `win_arm64` wheels on 43.x–45.x and 46.0.4+).
  - **Why ChaCha20-Poly1305:** Runs in constant-time software/SIMD on every CPU (including older Intel/AMD/ARM64 without AES crypto extensions) at ~`<0.015 ms` per 40-byte input packet — keeping LAN input latency sub-millisecond (`<0.5 ms`).
  - **Packet Replay Protection:** `nonce = struct.pack("<II4x", epoch, seq)` (12 bytes; `epoch` increments every session, `seq` increments monotonically every packet) combined with existing 512-entry sliding deduplication window.

---

## 5. Canonical Wire Protocol & Bidirectional Key Mapping

### 5.1 Protocol v2 Framing (`protocol.py`)

```
Wire Packet (UDP):
┌───────────────────────────────────────┬──────────────────────────────────┬────────────────┐
│ Cleartext Header = 16 bytes (AAD)     │ Ciphertext (Variable, 0..N B)    │ Poly1305 Tag   │
│ magic(2B='MC') | ver(1B=2) | type(1B) │                                  │ (16 bytes)     │
│ epoch(4B u32)  | seq(4B u32)          │ Encrypted payload for pkt_type   │                │
│ sender_hash(4B = first 4B device SHA) │                                  │                │
└───────────────────────────────────────┴──────────────────────────────────┴────────────────┘
AEAD Nonce (12B) = epoch(4B) || seq(4B) || sender_hash(4B)  (reconstructed, not sent twice)
Total overhead = 32 bytes (MOUSE_MOVE = 40 bytes total; KEY_EVENT = 36 bytes total)
```

- **Packet Types (v2):**
  - `1 PING` (`<Q` `timestamp_ns`) / `2 PONG` (`<Q` `timestamp_ns`) — direct LAN IP probing & RTT measurement (replaces `HELLO` to central relay).
  - `10 MOUSE_MOVE` (`<ii` `os_x, os_y`) — absolute target coordinates on Slave virtual screen.
  - `12 MOUSE_BUTTON` (`<BBii` `button 1..5, state 0/1, os_x, os_y`).
  - `13 MOUSE_SCROLL` (`<ffB` `dx, dy, is_pixel`).
  - `20 KEY_EVENT` (`<HBB` **`canonical_hid_code: uint16`**, `state: 1=down/0=up`, `flags`).
  - `21 MEDIA_EVENT` (`<HB` **`canonical_consumer_code: uint16`**, `state: 1=down/0=up`).
  - `30 ENTER_SCREEN` (`<ii` `entry_os_x, entry_os_y`).
  - `31 LEAVE_SCREEN` (empty payload — release all held keys/buttons & park/restore).
  - `32 GESTURE_ACTION` (`<B` action code: `1=TASK_VIEW/MISSION_CONTROL`, `2=ESCAPE`, `3=LOCK_WORKSTATION`) — clean OS-independent gesture semantics!

> **Dual-Path Reliability Without Server Relay:**
> Previously, `reliable=True` duplicated state transitions (`ENTER_SCREEN`, `LEAVE_SCREEN`, `MOUSE_BUTTON`, `KEY_EVENT`) onto the Server UDP Relay (`8766`). Without a central server, `reliable=True` sends the UDP packet **immediately** AND mirrors a lightweight `{ "type": "reliable_input", "pkt_b64": ... }` frame over the already-open **TLS WebSocket (`8768`)** (or repeats UDP once at +4ms if WS send queue is busy). Because both paths share the exact same `(epoch, seq)` and the receiver checks its 512-entry `_seen_seq_set`, state changes are **guaranteed never to drop or double-fire** even on lossy Wi-Fi!

### 5.2 Canonical Keycode Translation (USB HID Usage IDs)

Instead of v1's one-way `MAC_KEYCODE_TO_WIN_VK`, v2 uses **USB HID Usage IDs (Page 0x07)** as the canonical wire keycode:

- **Capture side:**
  - `darwin` (`CGKeyCode`) → `HID_Usage` (`0x04..0xE7`)
  - `windows` (`(vk_code, scan_code, is_extended)`) → `HID_Usage`
    - *Key insight:* Using Windows hardware **Scan Code** (`KBDLLHOOKSTRUCT.scanCode` + `LLKHF_EXTENDED`) rather than just `vkCode` makes key mapping accurate across QWERTY/AZERTY/QWERTZ/Thai/Japanese physical layouts AND distinguishes Left/Right modifiers (`0xE0`–`0xE7`), Return vs Numpad Enter (`0x28` vs `0x58`), and Arrow/Nav cluster vs Numpad!
    - Special Windows **AltGr** handling: On ISO/European Windows layouts, pressing Right Alt emits a synthetic `VK_LCONTROL (0x11/0xA2)` down immediately followed by `VK_RMENU (0xA5)` (`LLKHF_EXTENDED`). `WinInputController` detects and filters the synthetic `LControl` companion so `AltGr` works cleanly on Mac/Win Slaves without sticking `Ctrl`.
- **Receiver side (`HID_Usage` → Local OS key):**
  - Translates `HID_Usage` → `CGKeyCode` (on macOS) or `(win_vk, win_scancode, extended)` (on Windows).
  - Applies **Role-Aware Modifier Remapping** per `(master_os, slave_os)`:

| Pair (`Master → Slave`) | Default `swap_cmd_ctrl` | Behavior |
| :--- | :--- | :--- |
| **`Mac → Windows`** | `True` | Mac `⌘ Cmd` → Win `Ctrl`; Mac `⌃ Ctrl` → Win `⊞ Win` (so `⌘C`/`⌘V` copies/pastes on Windows). |
| **`Windows → Mac`** | `True` | Win `Ctrl` → Mac `⌘ Cmd`; Win `⊞ Win` → Mac `⌃ Ctrl` (or `⌥ Opt` if user prefers; so `Ctrl+C`/`Ctrl+V` copies/pastes on Mac!). |
| **`Mac → Mac`** | `False` | 1:1 native (`⌘ Cmd → ⌘ Cmd`, `⌃ Ctrl → ⌃ Ctrl`, `⌥ Opt → ⌥ Opt`). |
| **`Windows → Windows`** | `False` | 1:1 native (`Ctrl → Ctrl`, `Win → Win`, `Alt → Alt`). |

- **Gesture & Media Translation:**
  - Volume/Play/Next/Prev (`0x0C` Consumer Page) → `NX_SYSDEFINED` on Mac Slave; `VK_VOLUME_*` / `VK_MEDIA_*` on Win Slave.
  - 3-Finger Swipe Up (Mac Master) → `Win+Tab` on Win Slave, or `Ctrl+↑` (`Mission Control`) on Mac Slave.

---

## 6. macOS Bidirectional Backend (`mac_backend.py`)

### 6.1 Capture Side Refinements (`MacInputController` — Master Role)

1. **Graceful Trackpad Fallback (Support Most Macs):**
   - Macs without a built-in Force Touch trackpad (Mac mini, Mac Studio, Mac Pro, iMac, or MacBook in clamshell mode with external mouse) return 0 devices from `MTDeviceCreateList()`.
   - Make `_init_multitouch_trackpad()` strictly optional (`if not self._mt_devices: log info and continue`) and re-scan safely when external Magic Trackpads connect/disconnect without crashing.
2. **Display Hot-Plug Listener:**
   - Register `CGDisplayRegisterReconfigurationCallback` so plugging/unplugging an external monitor or opening/closing a MacBook lid automatically refreshes `DisplayRect[]` and syncs the updated layout to the peer.
3. **TCC Permission Verification:**
   - Check `Quartz.AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})` and `Quartz.CGPreflightListenEventAccess()` / `CGPreflightPostEventAccess()`.
   - Expose permission status (`accessibility_granted`, `input_monitoring_granted`, `local_network_ok`) via `/api/state` so the Dashboard shows a clear one-click "Open System Settings" checklist instead of silent failure.

### 6.2 New macOS Injection (`MacInputInjector` — Slave Role)

Implemented in `client/mac_backend.py` using `pyobjc-framework-Quartz` + `AppKit`:

1. **Mouse Movement & Dragging:**
   - Maintain `_held_buttons: Set[int]` and `_last_pos: Tuple[float, float]`.
   - Choose exact CoreGraphics event type from held state:
     - No button held → `kCGEventMouseMoved`
     - Button 1 held → `kCGEventLeftMouseDragged`
     - Button 2 held → `kCGEventRightMouseDragged`
     - Button 3/4/5 held → `kCGEventOtherMouseDragged`
   - Create event with `CGEventCreateMouseEvent(source, ev_type, (x, y), button_enum)`, compute `(dx, dy)` relative to `_last_pos` and set `kCGMouseEventDeltaX` / `kCGMouseEventDeltaY` (crucial for 3D viewports, games, and scroll-drag!), then call `CGEventPost(kCGHIDEventTap, ev)`.
   - On `ENTER_SCREEN`: call `CGWarpMouseCursorPosition((entry_x, entry_y))` + ensure cursor visible (`_show_cursor_now()`), followed by a `kCGEventMouseMoved` at `(entry_x, entry_y)` so hover states activate immediately.
2. **Multi-Click (`clickState`) & Extra Mouse Buttons:**
   - Track `(last_click_button, last_click_pos, last_click_ts, click_count)`. If the same button clicks within `AppKit.NSEvent.doubleClickInterval()` (~0.5s) and `<= 4 px` distance, increment `click_count` (1→2→3), and set `Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGMouseEventClickState, click_count)` on both down and up events so double-click word select and Finder folder opening work natively.
   - For buttons 3/4/5, set `kCGMouseEventButtonNumber` (`2` = middle, `3` = X1/Back, `4` = X2/Forward).
3. **Smooth Pixel & Line Scrolling:**
   - Use `Quartz.CGEventCreateScrollWheelEvent2(None, unit, 2, int(round(dy_scaled)), int(round(dx_scaled)), 0)`:
     - `kCGScrollEventUnitPixel` (`0`) when `is_pixel=True` (trackpad).
     - `kCGScrollEventUnitLine` (`1`) when `is_pixel=False` (mouse wheel, `dy * 3` lines).
   - Note: Check macOS vertical/horizontal sign conventions (Win `WHEEL_DELTA` positive up vs macOS `CGEventScrollWheelEvent2` positive up) and apply `invert_scroll_remote` cleanly.
4. **Keyboard Injection & Cumulative Modifier Flags (`CGEventFlags`):**
   - Maintain `_active_flags: int` (bitmask of `kCGEventFlagMaskShift`, `Control`, `Alternate`, `Command`, `AlphaShift`) + `_held_keys: Set[int]`.
   - When a modifier HID code arrives:
     - Update `_active_flags` bitmask AND post `CGEventCreateKeyboardEvent(source, cg_keycode, is_down)` with `CGEventSetFlags(ev, _active_flags)` (for CapsLock, toggle `AlphaShift` cleanly on down).
   - When a regular key arrives:
     - Create `ev = CGEventCreateKeyboardEvent(source, cg_keycode, is_down)`
     - Set `CGEventSetFlags(ev, _active_flags)` (macOS apps like Safari/VSCode check `CGEventGetFlags` on the key event itself for `⌘C`/`⌘V`, not just prior modifier events!)
     - Post via `CGEventPost(kCGHIDEventTap, ev)`.
   - Special Mac Lock shortcut: If Master sends `Lock` gesture or `Ctrl+Cmd+Q` (`HID Lock`), trigger lock cleanly (`pmset displaysleepnow` or `CGSession` lock via private `SACLockScreenImmediate` in `login.framework` if desired — or pass `Cmd+Ctrl+Q` directly which macOS honors natively via `CGEventPost`!).
5. **Media & Volume Keys (`NX_SYSDEFINED`):**
   - Synthesize via `AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_`:
     - `type = 14` (`NSSystemDefined`), `subtype = 8`, `modifierFlags = 0xA00 if down else 0xB00`, `data1 = (nx_key << 16) | ((0xA if down else 0xB) << 8)`, `data2 = -1`, then `Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev.CGEvent())`.
6. **`LEAVE_SCREEN` Cleanup:**
   - Release all `_held_buttons`, post key-up for all `_held_keys`, reset `_active_flags = 0`, and park Mac cursor cleanly at an innocuous edge midpoint `(park_x, park_y)` if desired or leave at last coordinate.

### 6.3 Supported Mac Hardware & OS Compatibility

- **CPU Architectures:** Apple Silicon (`arm64`: M1/M2/M3/M4+) and Intel (`x86_64`) via single `universal2` `.app` / `.dmg`.
- **macOS Versions:** macOS 12 Monterey, 13 Ventura, 14 Sonoma, 15 Sequoia, and 26 Tahoe.
- **Notch & Retina Scaling:** `CGDisplayBounds` already returns logical points (e.g., `1470x956` or `1512x982`) matching `CGEventGetLocation` and `CGWarpMouseCursorPosition` coordinates seamlessly; `scale_factor` records physical backing scale (`2.0`).

### 6.4 macOS Permissions, Sequoia Local Network Privacy & Constraints

Every permission constraint must be surfaced clearly in the Dashboard onboarding UI and documentation:

| macOS Constraint | Impact | Mitigation in Plan |
| :--- | :--- | :--- |
| **Accessibility (`TCC`)** | Mandatory for both `CGEventTap` (Master) and `CGEventPost` (Slave). Without it, input capture/injection silently fails. | Check `AXIsProcessTrustedWithOptions` on startup; display an interactive Onboarding Banner in Dashboard with a button opening `Privacy_Accessibility`. |
| **Input Monitoring (`TCC`)** | Needed on macOS 10.15+ for `kCGHIDEventTap` listening when Master. | Call `CGRequestListenEventAccess()` alongside Accessibility prompt. |
| **Local Network Privacy (macOS 15 Sequoia+)** | Sequoia silently drops LAN sockets & mDNS (`EHOSTUNREACH`) unless `.app/Contents/Info.plist` includes usage keys. | Embed `NSLocalNetworkUsageDescription` and `NSBonjourServices` (`_multicontrol._tcp`) in `Info.plist`; detect bind/multicast failure and guide user to *Privacy & Security → Local Network*. |
| **Secure Keyboard Entry / Lock Screen / FileVault** | macOS intentionally blocks synthetic `CGEventPost` inside Terminal Secure Input mode, `sudo` password prompts, and FileVault/LoginWindow screens (unless an Apple-signed `CGPreLoginApp` daemon is used). | Monitor session lock state (`Quartz.CGSessionCopyCurrentDictionary()` → `CGSSessionScreenIsLocked`). When Mac Slave locks, notify Master ("Mac screen is locked — unlock locally to resume") and automatically release held keys. |

---

## 7. Windows Bidirectional Backend (`win_backend.py` & Privilege Split)

### 7.1 New Windows Capture (`WinInputController` — Master Role)

Implemented in `client/win_backend.py` on a dedicated high-priority thread running a Win32 message pump (`GetMessageW`):

1. **Dual-Pipeline Capture (`WH_MOUSE_LL` + `WH_KEYBOARD_LL` + Raw Input `WM_INPUT`):**
   - **`WH_KEYBOARD_LL` (`SetWindowsHookExW(13, ...)`):**
     - Ignore events with `LLKHF_INJECTED` (`0x10`) to prevent feedback loops.
     - Check Emergency Return Hotkey **first** (e.g., `Ctrl + Alt + Pause`, or configurable `Ctrl + Alt + Shift + Esc` — avoiding `Ctrl+Alt+Esc` which switches windows and `Ctrl+Shift+Esc` which opens Task Manager).
     - When `remote_active == True`: translate `(vkCode, scanCode, flags & LLKHF_EXTENDED)` → `CanonicalHID`, queue UDP send, and **return `1`** to swallow locally (intercepts `Alt+Tab`, `Win` key, `Win+D`, Media keys, etc. — note: Windows kernel intercepts `Ctrl+Alt+Del` SAS before hooks, which is unavoidable in user-mode and documented).
   - **`WH_MOUSE_LL` (`SetWindowsHookExW(14, ...)`):**
     - Ignore `LLMHF_INJECTED` (`0x01`).
     - When `remote_active == False` (cursor on Windows): track cursor position `(pt.x, pt.y)` + compute `(dx, dy)` delta against previous point (or `WM_INPUT`) and feed `VirtualCanvasEngine.step_from_native(...)` to detect edge crossings into the Slave screen!
     - When `remote_active == True` (cursor on Slave): swallow `WM_MOUSEMOVE`, `WM_LBUTTON*`, `WM_RBUTTON*`, `WM_MBUTTON*`, `WM_XBUTTON*`, `WM_MOUSEWHEEL`, `WM_MOUSEHWHEEL` (return `1`) and forward clicks/scrolls to Slave.
   - **Raw Input (`WM_INPUT` via `RegisterRawInputDevices` with `RIDEV_INPUTSINK` on a hidden `HWND_MESSAGE` window):**
     - Why: Because `WH_MOUSE_LL` runs *after* Raw Input in the Windows input pipeline, suppressing `WM_MOUSEMOVE` in `WH_MOUSE_LL` freezes the local Windows cursor **while `WM_INPUT` continues delivering true, unaccelerated, unbounded hardware `(lLastX, lLastY)` deltas!**
     - This cleanly solves Windows DPI scaling distortion and edge-clamping while controlling a Slave screen.
2. **Hook Timeout & Performance Safety:**
   - Windows enforces `LowLevelHooksTimeout` (~300–1000 ms); slow hook callbacks freeze the mouse and cause Windows to silently drop the hook.
   - **Rule:** Hook callbacks do **zero** blocking I/O, logging, or clipboard calls. They push `(event_tuple)` into a thread-safe `queue.SimpleQueue` and signal a worker thread (or non-blocking UDP `sendto`) in `<50 µs`.
3. **Crash-Safe Cursor Hiding (No Global `SetSystemCursor` Mutation):**
   - Rather than overwriting global system `OCR_*` cursors via `SetSystemCursor` (which leaves the user with an invisible cursor if the process crashes or is killed in Task Manager), `WinInputController` uses a **local layered trap window**:
     - Create a tiny `5x5` pixel `WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED` window (`SetLayeredWindowAttributes(..., alpha=1, LWA_ALPHA)` — visually invisible) whose `WNDCLASS.hCursor` is a `1x1` transparent `HCURSOR` (`CreateCursor` with empty AND/XOR masks).
     - On `enter_remote_mode()`: move this `5x5` window to `(park_x - 2, park_y - 2)`, `ShowWindow(SW_SHOWNOACTIVATE)`, warp cursor inside it `(park_x, park_y)` and set `ClipCursor` to that `1x1` center point while `WH_MOUSE_LL` swallows `WM_MOUSEMOVE` and `WM_INPUT` streams deltas!
     - On `leave_remote_mode()` or process crash: `ClipCursor(None)` + `DestroyWindow` immediately restores the normal desktop cursor with **zero persistent OS cursor registry corruption**.

### 7.2 Windows Privilege Split (User-Mode Default + Optional SYSTEM Helper)

In v1, `service_watchdog.py` launched the entire agent (`tray.py` + `multicontrol_agent.py`) as `NT AUTHORITY\SYSTEM`. Launching a user's default web browser from a `SYSTEM` process is a security hazard and opens an empty `SYSTEM` browser profile.

**v2 Clean Split Architecture:**

```
┌───────────────────────────────────────────┐      Named Pipe (ACL: User + SYSTEM)      ┌────────────────────────────────────────┐
│  MultiControl.exe (User Session Process)  │  ◄──────────────────────────────────────► │  MultiControlService (SYSTEM Watchdog) │
│  • Tray Icon & Default Browser Dashboard  │   \\.\pipe\MultiControlHelper             │  (Optional — Installed only if toggled)│
│  • mDNS, TLS WS, ChaCha20 UDP Socket      │   Binary frame: move/click/key/lock       │  • Spawns MultiControl.exe --helper    │
│  • Clipboard & Layout Engine              │                                           │    in active session (winlogon token)  │
│  • In-process Capture/Inject (User Mode)  │                                           │  • SetThreadDesktop(Winlogon/Default)│
└───────────────────────────────────────────┘                                           │  • SendInput() + LockWorkStation()     │
                                                                                        └────────────────────────────────────────┘
```

- **Default Install (User Mode):** No `SYSTEM` service installed; no Admin UAC prompt required; works on restricted/standard Windows accounts. Covers all standard user desktop apps.
- **Optional "Lock Screen & Elevated/UAC Control" Mode:**
  - Can be enabled via installer checkbox OR toggled anytime inside Dashboard Settings (triggers single UAC elevation prompt to register `MultiControlService`).
  - When active and `User Process` is Slave, `User Process` forwards verified decrypted input frames over `\\.\pipe\MultiControlHelper` to `MultiControl.exe --helper` (running in the interactive console session with `winlogon.exe` System Integrity) to inject into `LogonUI.exe` Lock/PIN screens and elevated UAC prompts.
  - **Cold-Boot Pre-Login Support:** When `MultiControlService` detects no logged-in user session yet (`Winlogon` cold boot), `--helper` can directly bind the trusted peer TLS/UDP endpoint using the synced machine trust store in `C:\ProgramData\MultiControl\` until user logon completes, then hand control back to the user-session process cleanly!
- **Frozen Binary Support:** Fix `service_watchdog.py` so `if getattr(sys, "frozen", False): cmd = f'"{sys.executable}" --helper'` (no hardcoded `venv\Scripts\pythonw.exe` in packaged builds).

### 7.3 Windows 10/11 Multi-Monitor, Mixed DPI & ARM64 Notes

- `SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)` (`-4`) remains called at very start of process before any `user32` display/hook calls.
- Virtual screen negative coordinates (`SM_XVIRTUALSCREEN < 0` when secondary display is left/above primary) are supported in both `EnumDisplayMonitors`, `SendInput(MOUSEEVENTF_VIRTUALDESK)`, and `WH_MOUSE_LL`.
- **Windows ARM64 (`win_arm64`):** All `ctypes` Win32 signatures already use explicit `argtypes`/`restype` and pointer-sized `ULONG_PTR = wintypes.WPARAM` (64-bit LLP64 ABI matches x64). Packaging builds natively on `windows-11-arm` with native ARM64 Python.
- **Windows Firewall:** Privileged setup adds allow rules for TCP `8768`, UDP `8767`, UDP `8769`; User-mode relies on Windows Defender Firewall automatic prompt on first private-network listen (with clear Dashboard banner if blocked on Public Wi-Fi profile).

---

## 8. Embedded Local Dashboard & Master/Slave Switching

### 8.1 Embedded Local Web Server (`multicontrol/web_ui.py`)

- Runs inside the background `asyncio` thread using **Starlette + Uvicorn** (pure Python `h11` HTTP + `websockets` — zero `fastapi`/`pydantic-core` Rust dependency!).
- Subclasses `uvicorn.Server` with `install_signal_handlers = lambda self: None` so Uvicorn runs cleanly inside the background thread while macOS AppKit / Win32 Tray owns the main thread.
- Bound strictly to `127.0.0.1:8765` (with ephemeral port fallback `8765..8780` if busy; Tray opens exact bound URL). Secured with a local per-launch session cookie/header or localhost-only bind so other LAN devices cannot hit local control endpoints.

### 8.2 Dashboard UX Enhancements (Upgrading `server/static/*`)

1. **Top Bar Role Banner & 1-Click Switch:**
   - Displays current state pill: **`MASTER (Controlling Peer)`**, **`SLAVE (Controlled by Peer)`**, or **`UNPAIRED`**.
   - Prominent button on every machine's dashboard: **`👑 Make This Machine Master`** (disabled when already Master; clicking while Slave sends `CLAIM_MASTER` and promotes this machine in <100ms).
2. **New "Connect / Pair Peer" Drawer & Modal (Replacing Central Hub Commands):**
   - **Auto-Discovered Peers List:** Live list from mDNS + UDP beacon showing hostname, OS badge (` macOS` / `⊞ Windows`), IP, and **Pair** button.
   - **Manual IP Connect:** Input for `IP` (e.g. `192.168.1.50` or Tailscale `100.x.x.x`) + **Pair**.
   - **6-Digit PIN Modal:** Initiator types PIN displayed on the target machine's Dashboard/notification; shows **Paired & Encrypted (ChaCha20 + TLS)** badge once complete, plus **Unpair / Forget Device**.
3. **2D Screen Layout Studio (Retained & Enhanced):**
   - Keeps all existing canvas drag-and-snap, bottom-right physical size calibration, `Test Cursor Crossing` simulation, live cursor radar, and quick presets (`Left`, `Right`, `Above/Below`, `Align Centers`, `1:1`).
   - Updates labels dynamically from hardcoded `Mac (Master)` / `Windows (Target)` to **`This Machine (Master/Slave)`** and **`Peer Machine (Slave/Master)`**.
4. **Role-Aware Settings Sidebar:**
   - Dynamically adapts hints/toggles to the active Master → Slave pair:
     - Shows **macOS Trackpad Gestures** section whenever a Mac is involved as Master.
     - Shows **Modifier Swap (`Ctrl ↔ Cmd` / `Opt ↔ Win`)** with clear wording for whichever OS is currently Master (`Mac→Win` vs `Win→Mac` vs `Same-OS 1:1`).
     - Shows **Emergency Return Hotkey** matching the current Master OS (`Ctrl + Opt + Esc` on Mac Master; `Ctrl + Alt + Pause` on Windows Master).
   - **System Integration Card:**
     - Toggle **Launch at Login / Boot**.
     - On Windows: Toggle **Lock Screen & Elevated UAC Control (Service Mode)** with status badge.
     - On macOS: Live permission checklist (**Accessibility**, **Input Monitoring**, **Local Network**) with one-click deep links into System Settings.

---

## 9. Packaging, End-User Installers & CI/CD Pipeline

### 9.1 Dependency Stack Verification (Zero Rust / Build-Toolchain Traps)

Every dependency was verified for prebuilt wheel availability across **`macosx_universal2`**, **`win_amd64`**, and **`win_arm64`**:

| Package | Purpose | `macosx_universal2` | `win_amd64` | `win_arm64` |
| :--- | :--- | :--- | :--- | :--- |
| `starlette` | Embedded local Dashboard routes & static serving | Pure Python (`any`) | Pure Python (`any`) | Pure Python (`any`) |
| `uvicorn` + `h11` | Embedded ASGI server (no `[standard]` / no `uvloop` / no `httptools`) | Pure Python (`any`) | Pure Python (`any`) | Pure Python (`any`) |
| `websockets` | Local UI WS & P2P TLS WS control plane | Universal2 wheel / Pure fallback | Prebuilt wheel | Pure Python fallback |
| `pycryptodome` | ChaCha20-Poly1305 UDP AEAD + ECDSA/X.509 cert gen | `universal2` wheel | `win_amd64` wheel | `win_arm64` wheel |
| `zeroconf` | mDNS / DNS-SD LAN discovery (`--no-binary zeroconf` safe fallback) | Pure Python (`any`) / wheel | Prebuilt wheel | Pure Python fallback |
| `ifaddr` | Reliable multi-interface LAN/VPN IP enumeration | Pure Python (`any`) | Pure Python (`any`) | Pure Python (`any`) |
| `pystray` | Menu Bar / System Tray icon | Pure Python (uses PyObjC) | Pure Python (Win32 ctypes) | Pure Python (Win32 ctypes) |
| `Pillow` | Tray icon rendering | `universal2` wheel | `win_amd64` wheel | `win_arm64` wheel |
| `pyobjc-framework-Quartz` + `Cocoa` | macOS display enum, `CGEventTap`, `CGEventPost`, `NSEvent` *(macOS only)* | `universal2` wheel | N/A | N/A |

> **Why `fastapi` was replaced by `starlette`:** `fastapi` pulls `pydantic` → `pydantic-core` (Rust), increasing bundle size and risk on `win_arm64` / `universal2`. `starlette` is pure Python and provides the exact ASGI routing, `StaticFiles`, `JSONResponse`, and `WebSocket` APIs we use.
> **Why `pycryptodome` over `cryptography`:** Upstream `cryptography` did not publish `win_arm64` wheels on PyPI for 43.x–45.x or 46.0.4+. `pycryptodome` publishes official `win_arm64` and `macosx_universal2` wheels with zero Rust toolchain requirement. (Can optionally swap to pinned `cryptography==46.0.3` if ever required).

### 9.2 macOS Packaging (`MultiControl.app` → `MultiControl-macOS-Universal2.dmg`)

- **Build Host Requirement:** Official **Python.org macOS `universal2` installer** (NOT Homebrew Python, which is single-arch) on Apple Silicon `macos-latest`.
- **Bundler:** PyInstaller `--onedir --windowed --target-arch universal2` (never `--onefile`, which breaks macOS bundle signing and framework loading).
- **Verification Gate:** `lipo -info MultiControl.app/Contents/MacOS/MultiControl` must report `Architectures in the fat file: x86_64 arm64`.
- **`Info.plist` Required Keys:**
  - `CFBundleIdentifier`: `com.multicontrol.app`
  - `LSMinimumSystemVersion`: `12.0`
  - `LSUIElement`: `true` (runs cleanly in Menu Bar without Dock clutter)
  - `NSLocalNetworkUsageDescription`: *"MultiControl uses your local network to discover and control your paired computer."*
  - `NSBonjourServices`: `["_multicontrol._tcp"]`
  - `NSAccessibilityUsageDescription`: *"MultiControl needs Accessibility access to capture and inject mouse and keyboard events between your two computers."*
- **Installer Format:** Drag-and-drop `.dmg` created via `create-dmg` / `hdiutil` (`MultiControl.app` + `/Applications` symlink).
- **Signing & Gatekeeper:**
  - If CI secrets (`APPLE_DEVELOPER_ID`, `APPLE_NOTARY_KEY`) are present: `codesign --deep --force --options runtime --entitlements entitlements.plist` + `xcrun notarytool submit --wait` + `xcrun stapler staple`.
  - If unsigned (community/local builds): ad-hoc sign `codesign --force --deep --sign -` and display clear 2-step first-run instructions in README & Release Notes (*Right-click → Open* or *System Settings → Privacy & Security → Open Anyway*).

### 9.3 Windows Packaging (`MultiControl-Setup-x64.exe` & `MultiControl-Setup-arm64.exe`)

- **Build Hosts:** PyInstaller is **not** a cross-compiler:
  - Build x64 bundle on `windows-latest` with x64 Python.
  - Build ARM64 bundle on `windows-11-arm` with native ARM64 Python (`platform.machine() == "ARM64"`).
- **Bundler:** PyInstaller `--onedir --windowed --uac-admin=False` (default user-level). Include hidden imports for `uvicorn.logging`, `uvicorn.loops.auto`, `uvicorn.protocols.http.auto`, `uvicorn.protocols.websockets.auto`, `uvicorn.lifespan.on`.
- **Installer:** **Inno Setup 6 (`ISCC.exe`)** producing `MultiControl-Setup-x64.exe` and `MultiControl-Setup-arm64.exe`:
  - `PrivilegesRequired=lowest` with `PrivilegesRequiredOverridesAllowed=dialog` (defaults to per-user `%LOCALAPPDATA%\Programs\MultiControl` with zero Admin UAC needed; user can optionally pick system-wide install).
  - Optional post-install / settings task: **Enable Lock Screen & UAC Service Mode** (runs elevated `MultiControl.exe --install-service` to create `MultiControlService` `SYSTEM` task + Windows Firewall allow rules).
  - Auto-start on login via `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (User Mode) or `MultiControlService` (Service Mode).
- **Signing & SmartScreen:**
  - Optional Authenticode signing hook (`signtool.exe` / Azure Trusted Signing) when `WIN_CERT_PFX` secret is provided; otherwise documented SmartScreen *"More info → Run anyway"* guidance + SHA-256 checksums published in GitHub Releases.

### 9.4 Automated GitHub Actions Release Pipeline & Update Check

- **`.github/workflows/release.yml`:**
  - 3 parallel matrix build jobs: `macos-universal2` (`macos-latest`), `windows-x64` (`windows-latest`), `windows-arm64` (`windows-11-arm`).
  - Runs `python -m unittest discover -s tests -v` on each platform before packaging.
  - Uploads `MultiControl-macOS-Universal2.dmg`, `MultiControl-Windows-x64-Setup.exe`, `MultiControl-Windows-arm64-Setup.exe`, and `SHA256SUMS.txt` to GitHub Releases.
- **Lightweight Update Check:** Non-blocking background check (manual "Check for Updates" button or once-daily passive check against GitHub Releases `/releases/latest` API, toggleable off) that displays a link to download the newest installer — no fragile self-replacing binary updater needed in v2.0.

---

## 10. Repository Cleanup & Migration Map

### 10.1 Remove (Central Server & Host-Specific Artifacts)

- Delete `server/main.py`, `server/__init__.py`
- Delete `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- Delete `scripts/multicontrol.service`, `scripts/start-docker.sh`, `scripts/run-local.sh`
- Untrack/ignore `data/state.json` (contains legacy `192.168.1.100` / `linux-server` / `mac-host` / `windows-pc` dev state)

### 10.2 Proposed Target Package Layout

```text
multiControl/
├── multicontrol/
│   ├── __init__.py
│   ├── app.py                 # Entrypoint (--tray, --headless, --helper, --install-service)
│   ├── agent.py               # Symmetric P2P Master/Slave state machine & session coordinator
│   ├── config.py              # AppData paths, state.json load/save (lamport rev), trusted_peers.json
│   ├── crypto.py              # Identity cert gen, PIN PBKDF2 challenge-response, HKDF, ChaCha20 UDP AEAD
│   ├── discovery.py           # AsyncZeroconf (_multicontrol._tcp) + UDP LAN broadcast + interface IPs
│   ├── layout_engine.py       # 2D Virtual Canvas & portal math (migrated from client/)
│   ├── protocol.py            # Binary UDP v2 packets + USB HID Canonical Keycode <-> Mac/Win tables
│   ├── tray.py                # Role-aware Menu Bar / System Tray controller
│   ├── web_ui.py              # Embedded Starlette + Uvicorn local dashboard (127.0.0.1:8765)
│   ├── ui/                    # Upgraded Dashboard static files (migrated from server/static/)
│   │   ├── index.html
│   │   ├── app.js
│   │   └── styles.css
│   └── backends/
│       ├── __init__.py
│       ├── mac_backend.py     # MacInputController (Capture) + MacInputInjector (Injection) + TCC checks
│       ├── win_backend.py     # WinInputController (WH_*_LL + Raw Input + 5x5 trap) + WinInputInjector
│       └── win_helper.py      # Session-0 SYSTEM watchdog + Named Pipe IPC helper (migrated service_watchdog)
├── packaging/
│   ├── macos/
│   │   ├── multicontrol.spec
│   │   ├── Info.plist
│   │   ├── entitlements.plist
│   │   └── build_dmg.sh
│   └── windows/
│       ├── multicontrol.spec
│       ├── installer.iss      # Inno Setup script (x64 & arm64 parameterized)
│       └── build_win.ps1
├── .github/workflows/
│   ├── ci.yml                 # Unit tests across macOS + Windows x64/ARM64 on PRs
│   └── release.yml            # Builds .dmg + x64/arm64 Setup .exe + SHA256SUMS
├── tests/
│   ├── test_layout_and_protocol.py
│   ├── test_crypto_and_pairing.py
│   ├── test_peer_state_machine.py
│   ├── test_mac_backend.py
│   └── test_win_backend.py
├── requirements-core.txt
├── requirements-mac.txt
├── requirements-win.txt
└── README.md
```

---

## 11. Phased Implementation Roadmap & Verification Matrix

### Phase 1: Package Restructure & Central Server Removal
- Remove `server/main.py`, Docker files, and hardcoded `192.168.1.100` / `100.64.0.10` / `linux-server` references.
- Reorganize `client/` + `server/static/` into clean `multicontrol/` package with OS-standard `AppData`/`Application Support` storage.
- Split `requirements*.txt` (`core`, `mac`, `win`) using `starlette`, `uvicorn`, `h11`, `websockets`, `pycryptodome`, `zeroconf`, `ifaddr`, `pystray`, `Pillow`, `pyobjc-framework-Quartz/Cocoa`.

### Phase 2: Canonical Protocol v2 & Crypto Layer
- Implement `multicontrol/crypto.py`: ECDSA/RSA self-signed identity certs, cert SHA-256 pinning, 6-digit PIN PBKDF2 mutual challenge-response, HKDF session key derivation, and ChaCha20-Poly1305 AEAD encrypt/decrypt.
- Upgrade `multicontrol/protocol.py` to v2 packet framing + complete USB HID Usage ID bidirectional keycode maps (`Mac CGKeyCode ↔ HID ↔ Win (VK, ScanCode, Extended)`), including `AltGr` and media consumer codes.
- Write unit tests (`test_crypto_and_pairing.py`, updated `test_layout_and_protocol.py`) verifying round-trip encryption, tamper rejection, replay/wrap-around deduplication, and full keycode parity across all 4 OS pairs.

### Phase 3: LAN Discovery & Symmetric P2P Control Plane
- Implement `multicontrol/discovery.py` (`AsyncZeroconf` + UDP broadcast beacon `8769` + local IP enumeration via `ifaddr`).
- Implement `multicontrol/agent.py`: TLS WebSocket server/client (`8768/tcp`) with deterministic tie-breaker, PIN pairing state machine, bidirectional `sync_state` (`state_rev`), `CLAIM_MASTER` / `master_ack` role handoff, dual-path reliable state packet delivery, and disconnect fail-safe cursor recovery.
- Write unit/loopback integration tests (`test_peer_state_machine.py`) testing two simulated peers pairing, exchanging layouts, and flipping Master/Slave roles mid-session.

### Phase 4: macOS Bidirectional Backend (`mac_backend.py`)
- Add `MacInputInjector` (`CGEventCreateMouseEvent` with `clickState` & drag types, `CGEventCreateScrollWheelEvent2`, `CGEventCreateKeyboardEvent` + cumulative `CGEventFlags`, `NSEvent` `NX_SYSDEFINED` media injection).
- Harden `MacInputController` for trackpad-less Macs, external display hot-plug (`CGDisplayRegisterReconfigurationCallback`), and TCC/Local Network diagnostics (`AXIsProcessTrustedWithOptions`, `CGSSessionScreenIsLocked`).
- Add mocked unit tests (`test_mac_backend.py`) + manual hardware verification on Apple Silicon & Intel Macs.

### Phase 5: Windows Bidirectional Backend & Split Helper (`win_backend.py` + `win_helper.py`)
- Add `WinInputController` (`WH_MOUSE_LL` + `WH_KEYBOARD_LL` + `WM_INPUT` Raw Input `RIDEV_INPUTSINK` + `5x5` layered transparent trap window + `Ctrl+Alt+Pause` emergency return + AltGr filter).
- Refactor `service_watchdog.py` → `win_helper.py` supporting frozen `.exe --helper` and ACL-restricted `\\.\pipe\MultiControlHelper` IPC so the tray and browser remain strictly in the User Session.
- Expand `test_win_backend.py` with unit tests for hook injection filtering, AltGr handling, Raw Input delta decoding, and Named Pipe framing.

### Phase 6: Embedded Dashboard UI & Tray Upgrade
- Implement `multicontrol/web_ui.py` (Starlette + threaded Uvicorn on `127.0.0.1:8765`).
- Upgrade `multicontrol/ui/` (`index.html`, `app.js`, `styles.css`) with Nearby Peers discovery, Manual IP connect, 6-digit PIN modal, **Make Master** header control, dynamic OS-pair settings, macOS permissions checklist, and Windows Service Mode / Autostart toggles.
- Update `multicontrol/tray.py` with role-aware status, quick **Make Master**, and **Open Dashboard** actions.

### Phase 7: End-User Installers, CI/CD & Cross-Machine QA
- Author PyInstaller `.spec` files (`packaging/macos/multicontrol.spec`, `packaging/windows/multicontrol.spec`), `Info.plist`, `build_dmg.sh`, and Inno Setup `installer.iss`.
- Add `.github/workflows/ci.yml` and `release.yml` (verifying `lipo -info` for `universal2` and native `--only-binary=:all:` wheels on `win_arm64`).
- Update `README.md` with clear end-user installation guides, Gatekeeper/SmartScreen notes, and firewall troubleshooting.

### 11.1 Cross-Machine Manual Verification Matrix (Target Before Merging to `main`)

| Test Scenario | Expected Outcome |
| :--- | :--- |
| **Fresh Install on Mac (no Python) + Win PC (no Python)** | `.dmg` drags to `/Applications`; `.exe` Setup installs to `%LOCALAPPDATA%` without Admin; both dashboards open in browser. |
| **Auto-Discovery & PIN Pairing** | Both machines see each other on home/office LAN; 6-digit PIN pairs once; rebooting both auto-reconnects silently. |
| **Mac → Win Control (`Mac` Master)** | Cursor crosses seamlessly; `⌘C`/`⌘V` copies/pastes; 3-finger click & swipe work; media/volume keys control Windows; `Ctrl+Opt+Esc` returns cursor. |
| **Dashboard "Make Master" Flip (`Win` Master)** | Clicking **Make Master** on Windows dashboard immediately stops Mac capture, activates Win hooks, and lets Windows mouse/keyboard control Mac smoothly. |
| **Win → Mac Control (`Win` Master)** | Raw Input deltas move Mac cursor smoothly across Retina displays; `Ctrl+C`/`Ctrl+V` triggers `⌘C`/`⌘V` on Mac; double-click/drag work; `Ctrl+Alt+Pause` returns cursor; cursor never gets stuck invisible. |
| **Same-OS Pairs (`Mac↔Mac` & `Win↔Win`)** | Modifiers stay 1:1 unswapped; layout calibration & clipboard sync work cleanly. |
| **Fail-Safe & Lock Screen** | Killing/unplugging Slave while Master cursor is remote immediately restores Master cursor; enabling Windows optional Service Mode allows Lock/PIN screen entry without running browser as `SYSTEM`. |
