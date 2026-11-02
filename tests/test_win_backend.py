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


if __name__ == "__main__":
    unittest.main()
