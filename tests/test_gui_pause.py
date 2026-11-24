"""Headless checks for the desktop File menu pause/resume state."""

import threading
import unittest
from unittest.mock import Mock

from display.gui_display import DoomscrollGUI


class FeedPauseTests(unittest.TestCase):
    def make_gui_without_window(self):
        # Bypass Tk initialization so tests run without a desktop display.
        gui = DoomscrollGUI.__new__(DoomscrollGUI)
        gui.paused = threading.Event()
        gui.stopped = threading.Event()
        gui.file_menu = Mock()
        return gui

    def test_pause_toggles_to_resume_and_back(self):
        gui = self.make_gui_without_window()
        gui._toggle_feed_pause()
        self.assertTrue(gui.paused.is_set())
        gui.file_menu.entryconfigure.assert_called_with(0, label="Resume Feed")

        gui._toggle_feed_pause()
        self.assertFalse(gui.paused.is_set())
        gui.file_menu.entryconfigure.assert_called_with(0, label="Pause Feed")

    def test_shutdown_unblocks_worker_during_pause(self):
        gui = self.make_gui_without_window()
        gui.paused.set()
        gui.stopped.set()
        self.assertFalse(gui._wait_for_resume())

    def test_worker_proceeds_when_not_paused(self):
        gui = self.make_gui_without_window()
        self.assertTrue(gui._wait_for_resume())


if __name__ == "__main__":
    unittest.main()
