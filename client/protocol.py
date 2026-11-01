"""
Binary wire protocol and keycode translation between macOS (Quartz CGKeyCode)
and Windows (Win32 Virtual-Key codes & scan codes).

UDP Packet Format (Little-Endian):
  Header:
    magic:    2 bytes (b'MC')
    version:  1 byte  (1)
    pkt_type: 1 byte  (PacketType)
    seq:      4 bytes (uint32 sequence number)
    token:    8 bytes (first 8 bytes of SHA256 pairing token)
  Header size = 16 bytes (`<2sBBI8s`)
"""

from __future__ import annotations

import hashlib
import struct
from enum import IntEnum
from typing import Dict, Optional, Tuple

MAGIC = b"MC"
PROTOCOL_VERSION = 1
HEADER_STRUCT = struct.Struct("<2sBBI8s")


class PacketType(IntEnum):
    PING = 1
    PONG = 2
    HELLO = 3          # Peer registration on UDP port
    MOUSE_MOVE = 10    # Absolute target screen coords: <ii (x, y) + <B (monitor_idx)
    MOUSE_DELTA = 11   # Relative delta: <dd (dx, dy)
    MOUSE_BUTTON = 12  # <BBii (button: 1=left,2=right,3=middle,4=x1,5=x2; state: 1=down,0=up; x, y)
    MOUSE_SCROLL = 13  # <ddB (dx, dy, is_pixel: 1=pixel/trackpad, 0=line/wheel)
    KEY_EVENT = 20     # <HBB (vk_code: uint16, state: 1=down/0=up, flags: bit0=extended)
    ENTER_SCREEN = 30  # <ii (entry x, y on remote OS virtual desktop)
    LEAVE_SCREEN = 31  # No payload (release held keys/buttons & hide/park cursor)


MOUSE_Move_STRUCT = struct.Struct("<ii")
MOUSE_Delta_STRUCT = struct.Struct("<ff")
MOUSE_Button_STRUCT = struct.Struct("<BBii")
MOUSE_Scroll_STRUCT = struct.Struct("<ffB")
KEY_Event_STRUCT = struct.Struct("<HBB")
ENTER_Screen_STRUCT = struct.Struct("<ii")
PING_STRUCT = struct.Struct("<Q")  # timestamp_ns


def compute_token_digest(token: str) -> bytes:
    """Return 8-byte digest of the shared token for fast UDP packet authentication."""
    return hashlib.sha256((token or "multicontrol").encode("utf-8")).digest()[:8]


def pack_packet(pkt_type: int, seq: int, token_digest: bytes, payload: bytes = b"") -> bytes:
    return HEADER_STRUCT.pack(MAGIC, PROTOCOL_VERSION, int(pkt_type), seq & 0xFFFFFFFF, token_digest) + payload


def unpack_header(data: bytes) -> Optional[Tuple[int, int, bytes, bytes]]:
    if len(data) < HEADER_STRUCT.size:
        return None
    magic, version, pkt_type, seq, token_digest = HEADER_STRUCT.unpack_from(data, 0)
    if magic != MAGIC or version != PROTOCOL_VERSION:
        return None
    return pkt_type, seq, token_digest, data[HEADER_STRUCT.size :]


# ---------------------------------------------------------------------------
# macOS CGKeyCode -> Windows Virtual-Key (VK_*) Mapping
# ---------------------------------------------------------------------------
# Reference: HIToolbox/Events.h (kVK_*) -> WinUser.h (VK_*)

WIN_VK_LBUTTON = 0x01
WIN_VK_RBUTTON = 0x02
WIN_VK_BACK = 0x08
WIN_VK_TAB = 0x09
WIN_VK_RETURN = 0x0D
WIN_VK_SHIFT = 0x10
WIN_VK_CONTROL = 0x11
WIN_VK_MENU = 0x12      # Alt
WIN_VK_PAUSE = 0x13
WIN_VK_CAPITAL = 0x14   # Caps Lock
WIN_VK_ESCAPE = 0x1B
WIN_VK_SPACE = 0x20
WIN_VK_PRIOR = 0x21     # Page Up
WIN_VK_NEXT = 0x22      # Page Down
WIN_VK_END = 0x23
WIN_VK_HOME = 0x24
WIN_VK_LEFT = 0x25
WIN_VK_UP = 0x26
WIN_VK_RIGHT = 0x27
WIN_VK_DOWN = 0x28
WIN_VK_SNAPSHOT = 0x2C  # Print Screen
WIN_VK_INSERT = 0x2D
WIN_VK_DELETE = 0x2E
WIN_VK_LWIN = 0x5B
WIN_VK_RWIN = 0x5C
WIN_VK_APPS = 0x5D
WIN_VK_NUMPAD0 = 0x60
WIN_VK_NUMPAD1 = 0x61
WIN_VK_NUMPAD2 = 0x62
WIN_VK_NUMPAD3 = 0x63
WIN_VK_NUMPAD4 = 0x64
WIN_VK_NUMPAD5 = 0x65
WIN_VK_NUMPAD6 = 0x66
WIN_VK_NUMPAD7 = 0x67
WIN_VK_NUMPAD8 = 0x68
WIN_VK_NUMPAD9 = 0x69
WIN_VK_MULTIPLY = 0x6A
WIN_VK_ADD = 0x6B
WIN_VK_SUBTRACT = 0x6D
WIN_VK_DECIMAL = 0x6E
WIN_VK_DIVIDE = 0x6F
WIN_VK_F1 = 0x70
WIN_VK_F2 = 0x71
WIN_VK_F3 = 0x72
WIN_VK_F4 = 0x73
WIN_VK_F5 = 0x74
WIN_VK_F6 = 0x75
WIN_VK_F7 = 0x76
WIN_VK_F8 = 0x77
WIN_VK_F9 = 0x78
WIN_VK_F10 = 0x79
WIN_VK_F11 = 0x7A
WIN_VK_F12 = 0x7B
WIN_VK_F13 = 0x7C
WIN_VK_F14 = 0x7D
WIN_VK_F15 = 0x7E
WIN_VK_F16 = 0x7F
WIN_VK_F17 = 0x80
WIN_VK_F18 = 0x81
WIN_VK_F19 = 0x82
WIN_VK_F20 = 0x83
WIN_VK_NUMLOCK = 0x90
WIN_VK_SCROLL = 0x91
WIN_VK_LSHIFT = 0xA0
WIN_VK_RSHIFT = 0xA1
WIN_VK_LCONTROL = 0xA2
WIN_VK_RCONTROL = 0xA3
WIN_VK_LMENU = 0xA4     # Left Alt
WIN_VK_RMENU = 0xA5     # Right Alt
WIN_VK_VOLUME_MUTE = 0xAD
WIN_VK_VOLUME_DOWN = 0xAE
WIN_VK_VOLUME_UP = 0xAF
WIN_VK_MEDIA_NEXT_TRACK = 0xB0
WIN_VK_MEDIA_PREV_TRACK = 0xB1
WIN_VK_MEDIA_STOP = 0xB2
WIN_VK_MEDIA_PLAY_PAUSE = 0xB3
WIN_VK_OEM_1 = 0xBA       # ;:
WIN_VK_OEM_PLUS = 0xBB    # =+
WIN_VK_OEM_COMMA = 0xBC   # ,<
WIN_VK_OEM_MINUS = 0xBD   # -_
WIN_VK_OEM_PERIOD = 0xBE  # .>
WIN_VK_OEM_2 = 0xBF       # /?
WIN_VK_OEM_3 = 0xC0       # `~
WIN_VK_OEM_4 = 0xDB       # [{
WIN_VK_OEM_5 = 0xDC       # \|
WIN_VK_OEM_6 = 0xDD       # ]}
WIN_VK_OEM_7 = 0xDE       # '"

# Extended keys on Windows require KEYEVENTF_EXTENDEDKEY
EXTENDED_WIN_VKS = {
    WIN_VK_UP,
    WIN_VK_DOWN,
    WIN_VK_LEFT,
    WIN_VK_RIGHT,
    WIN_VK_HOME,
    WIN_VK_END,
    WIN_VK_PRIOR,
    WIN_VK_NEXT,
    WIN_VK_INSERT,
    WIN_VK_DELETE,
    WIN_VK_DIVIDE,
    WIN_VK_NUMLOCK,
    WIN_VK_RCONTROL,
    WIN_VK_RMENU,
    WIN_VK_LWIN,
    WIN_VK_RWIN,
    WIN_VK_APPS,
    WIN_VK_SNAPSHOT,
}

# macOS CGKeyCode -> Windows VK
MAC_KEYCODE_TO_WIN_VK: Dict[int, int] = {
    0x00: 0x41,  # A
    0x01: 0x53,  # S
    0x02: 0x44,  # D
    0x03: 0x46,  # F
    0x04: 0x48,  # H
    0x05: 0x47,  # G
    0x06: 0x5A,  # Z
    0x07: 0x58,  # X
    0x08: 0x43,  # C
    0x09: 0x56,  # V
    0x0A: WIN_VK_OEM_3,  # ISO Section
    0x0B: 0x42,  # B
    0x0C: 0x51,  # Q
    0x0D: 0x57,  # W
    0x0E: 0x45,  # E
    0x0F: 0x52,  # R
    0x10: 0x59,  # Y
    0x11: 0x54,  # T
    0x12: 0x31,  # 1
    0x13: 0x32,  # 2
    0x14: 0x33,  # 3
    0x15: 0x34,  # 4
    0x16: 0x36,  # 6
    0x17: 0x35,  # 5
    0x18: WIN_VK_OEM_PLUS,    # =
    0x19: 0x39,  # 9
    0x1A: 0x37,  # 7
    0x1B: WIN_VK_OEM_MINUS,   # -
    0x1C: 0x38,  # 8
    0x1D: 0x30,  # 0
    0x1E: WIN_VK_OEM_6,       # ]
    0x1F: 0x4F,  # O
    0x20: 0x55,  # U
    0x21: WIN_VK_OEM_4,       # [
    0x22: 0x49,  # I
    0x23: 0x50,  # P
    0x24: WIN_VK_RETURN,      # Return
    0x25: 0x4C,  # L
    0x26: 0x4A,  # J
    0x27: WIN_VK_OEM_7,       # '
    0x28: 0x4B,  # K
    0x29: WIN_VK_OEM_1,       # ;
    0x2A: WIN_VK_OEM_5,       # \
    0x2B: WIN_VK_OEM_COMMA,   # ,
    0x2C: WIN_VK_OEM_2,       # /
    0x2D: 0x4E,  # N
    0x2E: 0x4D,  # M
    0x2F: WIN_VK_OEM_PERIOD,  # .
    0x30: WIN_VK_TAB,         # Tab
    0x31: WIN_VK_SPACE,       # Space
    0x32: WIN_VK_OEM_3,       # `
    0x33: WIN_VK_BACK,        # Delete (Backspace)
    0x35: WIN_VK_ESCAPE,      # Escape
    0x36: WIN_VK_RWIN,        # Right Command
    0x37: WIN_VK_LWIN,        # Left Command
    0x38: WIN_VK_LSHIFT,      # Left Shift
    0x39: WIN_VK_CAPITAL,     # Caps Lock
    0x3A: WIN_VK_LMENU,       # Left Option (Alt)
    0x3B: WIN_VK_LCONTROL,    # Left Control
    0x3C: WIN_VK_RSHIFT,      # Right Shift
    0x3D: WIN_VK_RMENU,       # Right Option (Alt)
    0x3E: WIN_VK_RCONTROL,    # Right Control
    0x40: WIN_VK_F17,
    0x41: WIN_VK_DECIMAL,
    0x43: WIN_VK_MULTIPLY,
    0x45: WIN_VK_ADD,
    0x47: WIN_VK_NUMLOCK,     # Keypad Clear / NumLock
    0x48: WIN_VK_VOLUME_UP,
    0x49: WIN_VK_VOLUME_DOWN,
    0x4A: WIN_VK_VOLUME_MUTE,
    0x4B: WIN_VK_DIVIDE,
    0x4C: WIN_VK_RETURN,      # Keypad Enter
    0x4E: WIN_VK_SUBTRACT,
    0x4F: WIN_VK_F18,
    0x50: WIN_VK_F19,
    0x52: WIN_VK_NUMPAD0,
    0x53: WIN_VK_NUMPAD1,
    0x54: WIN_VK_NUMPAD2,
    0x55: WIN_VK_NUMPAD3,
    0x56: WIN_VK_NUMPAD4,
    0x57: WIN_VK_NUMPAD5,
    0x58: WIN_VK_NUMPAD6,
    0x59: WIN_VK_NUMPAD7,
    0x5A: WIN_VK_F20,
    0x5B: WIN_VK_NUMPAD8,
    0x5C: WIN_VK_NUMPAD9,
    0x60: WIN_VK_F5,
    0x61: WIN_VK_F6,
    0x62: WIN_VK_F7,
    0x63: WIN_VK_F3,
    0x64: WIN_VK_F8,
    0x65: WIN_VK_F9,
    0x67: WIN_VK_F11,
    0x69: WIN_VK_SNAPSHOT,    # F13 / PrintScreen
    0x6A: WIN_VK_F16,
    0x6B: WIN_VK_SCROLL,      # F14 / ScrollLock
    0x6D: WIN_VK_F10,
    0x6F: WIN_VK_F12,
    0x71: WIN_VK_PAUSE,       # F15 / Pause
    0x72: WIN_VK_INSERT,      # Help / Insert
    0x73: WIN_VK_HOME,
    0x74: WIN_VK_PRIOR,       # Page Up
    0x75: WIN_VK_DELETE,      # Forward Delete
    0x76: WIN_VK_F4,
    0x77: WIN_VK_END,
    0x78: WIN_VK_F2,
    0x79: WIN_VK_NEXT,        # Page Down
    0x7A: WIN_VK_F1,
    0x7B: WIN_VK_LEFT,
    0x7C: WIN_VK_RIGHT,
    0x7D: WIN_VK_DOWN,
    0x7E: WIN_VK_UP,
}


def mac_keycode_to_win_vk(
    mac_keycode: int,
    swap_cmd_ctrl: bool = True,
    swap_opt_win: bool = False,
) -> Optional[Tuple[int, int]]:
    """
    Convert a macOS CGKeyCode to (win_vk, flags).
    When swap_cmd_ctrl is True (default):
      - Mac Command (0x37/0x36) -> Windows Control (VK_LCONTROL / VK_RCONTROL)
      - Mac Control (0x3B/0x3E) -> Windows Key (VK_LWIN / VK_RWIN)
    So Cmd+C, Cmd+V, Cmd+A, Cmd+Z, Cmd+F work naturally on Windows!
    """
    vk = MAC_KEYCODE_TO_WIN_VK.get(mac_keycode)
    if vk is None:
        return None

    if swap_cmd_ctrl:
        if vk == WIN_VK_LWIN:
            vk = WIN_VK_LCONTROL
        elif vk == WIN_VK_RWIN:
            vk = WIN_VK_RCONTROL
        elif vk == WIN_VK_LCONTROL:
            vk = WIN_VK_LWIN
        elif vk == WIN_VK_RCONTROL:
            vk = WIN_VK_RWIN

    if swap_opt_win:
        if vk == WIN_VK_LMENU:
            vk = WIN_VK_LWIN
        elif vk == WIN_VK_RMENU:
            vk = WIN_VK_RWIN
        elif vk == WIN_VK_LWIN:
            vk = WIN_VK_LMENU
        elif vk == WIN_VK_RWIN:
            vk = WIN_VK_RMENU

    flags = 1 if vk in EXTENDED_WIN_VKS else 0
    return vk, flags
