"""
This script tests the PyBoy emulator setup by loading a save state and simulating button presses
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import keyboard
from pyboy.utils import WindowEvent

from autonomous_controller.emulator_session import open_emulator

# --- Paths ---
ROM_PATH = os.path.join("Pokemon_Red", "Red.gb")
SAVE_STATE_PATH = os.path.join("saves", "in-room-start.state")

# --- Launch Emulator ---
with open_emulator(ROM_PATH, window="SDL2") as state:
    state.set_emulation_speed(1)  # 1 = real-time

    # --- Load Save State ---
    with open(SAVE_STATE_PATH, "rb") as f:
        state.load_state(f)

    print("[INFO] Save state loaded. Player should be in room.")

    # --- Controller Wrapper ---
    class ControllerTest:
        """A simple wrapper to simulate button presses with timing."""

        def __init__(self, pyboy):
            self.pyboy = pyboy
            self.release_map = {
                WindowEvent.PRESS_ARROW_UP: WindowEvent.RELEASE_ARROW_UP,
                WindowEvent.PRESS_ARROW_DOWN: WindowEvent.RELEASE_ARROW_DOWN,
                WindowEvent.PRESS_ARROW_LEFT: WindowEvent.RELEASE_ARROW_LEFT,
                WindowEvent.PRESS_ARROW_RIGHT: WindowEvent.RELEASE_ARROW_RIGHT,
                WindowEvent.PRESS_BUTTON_A: WindowEvent.RELEASE_BUTTON_A,
                WindowEvent.PRESS_BUTTON_B: WindowEvent.RELEASE_BUTTON_B,
                WindowEvent.PRESS_BUTTON_START: WindowEvent.RELEASE_BUTTON_START,
            }

        def press(self, button, frames=12):
            """Press a button for a number of frames."""
            self.pyboy.send_input(button)
            for _ in range(frames):
                self.pyboy.tick()
            self.pyboy.send_input(self.release_map[button])

    # --- Instantiate controller ---
    controller = ControllerTest(state)

    # --- Test Movement ---
    print("[INFO] Testing movement...")
    controller.press(WindowEvent.PRESS_ARROW_RIGHT)
    controller.press(WindowEvent.PRESS_ARROW_DOWN)
    controller.press(WindowEvent.PRESS_ARROW_LEFT)
    controller.press(WindowEvent.PRESS_ARROW_UP)

    # --- Test Buttons ---
    controller.press(WindowEvent.PRESS_BUTTON_START)
    state.tick(2)
    controller.press(WindowEvent.PRESS_BUTTON_A)
    state.tick(2)
    controller.press(WindowEvent.PRESS_BUTTON_B)

    print("[INFO] Movement test done. Emulator running...")

    print("Press ESC to quit.")
    while not keyboard.is_pressed("esc"):
        state.tick()
