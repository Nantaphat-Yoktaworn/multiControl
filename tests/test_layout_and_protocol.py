"""Unit tests for the 2D Virtual Canvas edge-transition math and wire protocol."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from layout_engine import DisplayRect, LayoutSettings, VirtualCanvasEngine
from multicontrol_agent import _seq_is_newer
from protocol import (
    PacketType,
    WIN_VK_LCONTROL,
    WIN_VK_LWIN,
    compute_token_digest,
    mac_keycode_to_win_vk,
    pack_packet,
    unpack_header,
)


class TestMultiControlEngine(unittest.TestCase):
    def setUp(self) -> None:
        # Mac: 1512x982 on Left (vertically centered against a 1920x1080 Windows screen)
        self.mac = DisplayRect(
            id="mac:0",
            device_id="mac",
            device_name="MacBook Pro",
            os_type="darwin",
            monitor_index=0,
            name="Built-in Retina",
            primary=True,
            os_x=0.0,
            os_y=0.0,
            os_width=1512.0,
            os_height=982.0,
            canvas_x=0.0,
            canvas_y=49.0,
            canvas_width=1512.0,
            canvas_height=982.0,
        )
        # Windows: 1920x1080 on Right (touching Mac's right edge at x=1512)
        self.win = DisplayRect(
            id="win:0",
            device_id="win",
            device_name="Windows PC",
            os_type="windows",
            monitor_index=0,
            name="Windows Main",
            primary=True,
            os_x=0.0,
            os_y=0.0,
            os_width=1920.0,
            os_height=1080.0,
            canvas_x=1512.0,
            canvas_y=0.0,
            canvas_width=1920.0,
            canvas_height=1080.0,
        )
        self.engine = VirtualCanvasEngine([self.mac, self.win])

    def test_mac_to_win_right_edge_crossing(self) -> None:
        # Cursor at right edge of Mac (os_x=1511, os_y=491 -> canvas_y = 49 + 491 = 540, exact center of Win!)
        res = self.engine.step_from_native(
            cur_device_id="mac",
            os_x=1511.0,
            os_y=491.0,
            dx=8.0,
            dy=0.0,
            online_devices={"mac", "win"},
        )
        self.assertIsNotNone(res)
        self.assertTrue(res.device_changed)
        self.assertEqual(res.display.id, "win:0")
        # Should enter near left edge of Windows and vertically centered (os_y ~ 540)
        self.assertLessEqual(res.os_x, 15)
        self.assertAlmostEqual(res.os_y, 540, delta=2)

    def test_win_to_mac_left_edge_return(self) -> None:
        # Cursor on Windows near left edge (canvas_x=1514, canvas_y=540) moving left by dx=-10
        res = self.engine.step_virtual_delta(
            cur_display_id="win:0",
            cur_cx=1514.0,
            cur_cy=540.0,
            dx=-10.0,
            dy=0.0,
            online_devices={"mac", "win"},
        )
        self.assertIsNotNone(res)
        self.assertTrue(res.device_changed)
        self.assertEqual(res.display.id, "mac:0")
        # Should re-enter Mac near its right edge (os_x ~ 1504) and os_y ~ 491
        self.assertGreaterEqual(res.os_x, 1500)
        self.assertAlmostEqual(res.os_y, 491, delta=2)

    def test_physical_scale_calibration_mapping(self) -> None:
        # Suppose user shrinks Mac physical size on canvas to 50% height (491px tall)
        # aligned with the bottom half of Windows (canvas_y = 1080 - 491 = 589)
        self.mac.canvas_width = 756.0
        self.mac.canvas_height = 491.0
        self.mac.canvas_x = 1512.0 - 756.0
        self.mac.canvas_y = 1080.0 - 491.0

        # Leaving Mac at its vertical middle (os_y = 491 -> canvas_y = 589 + 245.5 = 834.5)
        res = self.engine.step_from_native(
            cur_device_id="mac",
            os_x=1511.0,
            os_y=491.0,
            dx=10.0,
            dy=0.0,
            online_devices={"mac", "win"},
        )
        self.assertIsNotNone(res)
        self.assertTrue(res.device_changed)
        self.assertEqual(res.display.id, "win:0")
        # Should enter Windows at os_y = 834 (lower-middle of Windows monitor, matching physical height!)
        self.assertAlmostEqual(res.os_y, 834, delta=3)

    def test_full_edge_crossing_including_corners(self) -> None:
        # Even near top-right corner of Mac (os_y = 5.0), cursor crosses seamlessly with 0px barrier
        res = self.engine.step_from_native(
            cur_device_id="mac",
            os_x=1511.0,
            os_y=5.0,
            dx=10.0,
            dy=0.0,
            online_devices={"mac", "win"},
        )
        self.assertIsNotNone(res)
        self.assertTrue(res.device_changed)
        self.assertEqual(res.display.id, "win:0")

    def test_mac_cmd_ctrl_swap(self) -> None:
        # Mac Left Command (0x37) -> Windows Left Control when swap_cmd_ctrl=True
        vk, _ = mac_keycode_to_win_vk(0x37, swap_cmd_ctrl=True)
        self.assertEqual(vk, WIN_VK_LCONTROL)
        # Mac Left Control (0x3B) -> Windows Left Win when swap_cmd_ctrl=True
        vk2, _ = mac_keycode_to_win_vk(0x3B, swap_cmd_ctrl=True)
        self.assertEqual(vk2, WIN_VK_LWIN)

    def test_udp_wire_packet(self) -> None:
        digest = compute_token_digest("secret-123")
        raw = pack_packet(PacketType.MOUSE_MOVE, 42, digest, b"\x01\x02\x03\x04")
        unpacked = unpack_header(raw)
        self.assertIsNotNone(unpacked)
        pkt_type, seq, rx_digest, payload = unpacked
        self.assertEqual(pkt_type, PacketType.MOUSE_MOVE)
        self.assertEqual(seq, 42)
        self.assertEqual(rx_digest, digest)
        self.assertEqual(payload, b"\x01\x02\x03\x04")

    def test_vertical_stacked_mac_below_ultrawide_windows(self) -> None:
        # User's real setup: Mac 1470x956 at (0, 98), Windows 3440x1440 at (-985, -1342)
        mac = DisplayRect(
            id="mac:0",
            device_id="mac",
            device_name="Mac",
            os_type="darwin",
            monitor_index=0,
            name="Mac Main",
            primary=True,
            os_x=0.0,
            os_y=0.0,
            os_width=1470.0,
            os_height=956.0,
            canvas_x=0.0,
            canvas_y=98.0,
            canvas_width=1470.0,
            canvas_height=956.0,
        )
        win = DisplayRect(
            id="win:0",
            device_id="win",
            device_name="Win",
            os_type="windows",
            monitor_index=0,
            name="Win Main",
            primary=True,
            os_x=0.0,
            os_y=0.0,
            os_width=3440.0,
            os_height=1440.0,
            canvas_x=-985.0,
            canvas_y=-1342.0,
            canvas_width=3440.0,
            canvas_height=1440.0,
        )
        eng = VirtualCanvasEngine([mac, win])
        # Push up against top edge of Mac (os_y=1.0, dy=-1.0) at horizontal center (os_x=735)
        res = eng.step_from_native("mac", 735.0, 1.0, 0.0, -1.0, {"mac", "win"})
        self.assertIsNotNone(res)
        self.assertTrue(res.device_changed)
        self.assertEqual(res.display.id, "win:0")
        self.assertAlmostEqual(res.os_x, 1720, delta=3)
        self.assertGreaterEqual(res.os_y, 1430)

        # Drag protection blocks accidental screen switch while dragging a window on Mac
        res_drag = eng.step_from_native(
            "mac", 735.0, 1.0, 0.0, -5.0, {"mac", "win"}, allow_transition=False
        )
        self.assertIsNotNone(res_drag)
        self.assertFalse(res_drag.device_changed)
        self.assertEqual(res_drag.display.id, "mac:0")

    def test_seq_is_newer(self) -> None:
        self.assertTrue(_seq_is_newer(1, -1))
        self.assertTrue(_seq_is_newer(10, 9))
        self.assertFalse(_seq_is_newer(9, 10))
        self.assertFalse(_seq_is_newer(10, 10))
        # Wrap-around across uint32 boundary
        self.assertTrue(_seq_is_newer(2, 0xFFFFFFFE))
        self.assertFalse(_seq_is_newer(0xFFFFFFFE, 2))


if __name__ == "__main__":
    unittest.main()
