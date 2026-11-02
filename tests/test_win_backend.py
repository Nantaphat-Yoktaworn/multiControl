"""Unit tests for Windows native backend, WinInputInjector, and desktop switching."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

import win_backend
from win_backend import WinInputInjector


class TestWinInputInjector(unittest.TestCase):
    def setUp(self) -> None:
        self.injector = WinInputInjector()

    def tearDown(self) -> None:
        self.injector.close()

    def test_initialization(self) -> None:
        self.assertEqual(len(self.injector._held_keys), 0)
        self.assertEqual(len(self.injector._held_buttons), 0)
        self.assertFalse(self.injector._privilege_enabled)

    def test_non_windows_noop(self) -> None:
        # On non-Windows platforms, methods safely return without exception
        self.assertTrue(self.injector.ensure_input_desktop(force=True))
        self.injector.move_cursor(100, 200)
        self.injector.mouse_button(1, 1)
        self.injector.mouse_button(1, 0)
        self.injector.mouse_scroll(0, 1.0, is_pixel=False)
        self.injector.key_event(0x41, 1)  # 'A'
        self.injector.key_event(0x41, 0)
        self.injector.release_all()
        self.injector.leave_screen()
        self.injector.close()

    def test_virtual_screen_bounds_fallback(self) -> None:
        bounds = self.injector._virtual_screen_bounds()
        self.assertEqual(len(bounds), 4)
        self.assertGreater(bounds[2], 0)
        self.assertGreater(bounds[3], 0)

    @patch("win_backend.IS_WINDOWS", True)
    @patch("win_backend.user32")
    @patch("win_backend.kernel32")
    def test_ensure_input_desktop_switch(self, mock_kernel, mock_user) -> None:
        mock_kernel.GetLastError.return_value = 0
        mock_user.OpenInputDesktop.return_value = 0x1234
        mock_user.SetThreadDesktop.return_value = True

        def fake_get_user_obj(h_desk, index, buf, size, needed):
            buf._obj.value = "Winlogon"
            return True

        mock_user.GetUserObjectInformationW.side_effect = fake_get_user_obj

        # Force desktop check
        res = self.injector.ensure_input_desktop(force=True)
        self.assertTrue(res)
        mock_user.OpenInputDesktop.assert_called()
        mock_user.SetThreadDesktop.assert_called_with(0x1234)

        # Calling again with same desktop name should reuse and close the temporary handle
        mock_user.OpenInputDesktop.return_value = 0x5678
        res2 = self.injector.ensure_input_desktop(force=True)
        self.assertTrue(res2)
        mock_user.CloseDesktop.assert_called_with(0x5678)

    @patch("win_backend.IS_WINDOWS", True)
    @patch("win_backend.user32")
    def test_move_cursor_and_key_event(self, mock_user) -> None:
        mock_user.MapVirtualKeyW.return_value = 0x02  # scancode for key '1'
        mock_user.GetSystemMetrics.side_effect = lambda m: {76: 0, 77: 0, 78: 1920, 79: 1080}.get(m, 0)
        with patch.object(self.injector, "ensure_input_desktop", return_value=True):
            self.injector.move_cursor(500, 300)
            mock_user.SetCursorPos.assert_called_with(500, 300)
            mock_user.SendInput.assert_called()

            # Test key_event with scancode flag (0x0008)
            self.injector.key_event(0x31, 1)  # key down '1'
            args = mock_user.SendInput.call_args[0]
            inp_ref = args[1]
            inp_obj = inp_ref._obj if hasattr(inp_ref, "_obj") else inp_ref.contents
            self.assertEqual(inp_obj.union.ki.wScan, 0x02)
            self.assertTrue(inp_obj.union.ki.dwFlags & 0x0008)  # KEYEVENTF_SCANCODE


if __name__ == "__main__":
    unittest.main()
