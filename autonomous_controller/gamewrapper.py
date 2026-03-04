"""
Defines the gamewrapper from in-built PyBoy, which is used to interact with the game.
"""

from pyboy import PyBoy
import keyboard
from pyboy.utils import WindowEvent  # pylint: disable=no-name-in-module
from memory_state.memory_reader import MemoryReader
state = PyBoy("Pokemon_Red/Red.gb", window="SDL2")
with open("saves\\in-room-start.state", "rb") as f:
    state.load_state(f)
game = state.game_wrapper
# print(game.enabled)

# --- Controller Wrapper ---
class ControllerTest:  # pylint: disable=too-few-public-methods
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

# # --- Test Movement ---
# print("[INFO] Testing movement...")
# controller.press(WindowEvent.PRESS_ARROW_RIGHT)
# controller.press(WindowEvent.PRESS_ARROW_DOWN)
# controller.press(WindowEvent.PRESS_ARROW_LEFT)
# controller.press(WindowEvent.PRESS_ARROW_UP)

# # --- Test Buttons ---
# controller.press(WindowEvent.PRESS_BUTTON_START)
# state.tick(2)
# controller.press(WindowEvent.PRESS_BUTTON_A)
# state.tick(2)
# controller.press(WindowEvent.PRESS_BUTTON_B)

def warps(state):
    mem = MemoryReader(state)
    warp_count = mem.read_byte(0xD36A)
    warp = []
    base = 0xD36B

    for i in range(warp_count):
        y = mem.read_byte(base + i*4)
        x = mem.read_byte(base + i*4 + 1)
        dest_warp = mem.read_byte(base + i*4 + 2)
        dest_map = mem.read_byte(base + i*4 + 3)
        if (x, y) not in [(0, 0), (255, 255), (0, 255), (255, 0)]:
            warp.append((x, y, dest_map, dest_warp))
    print(game.game_area_collision()) #type: ignore
    return warp

ctr = 0
while keyboard.is_pressed("esc") == False:
    state.tick()
    ctr += 1
    if ctr % 100 == 0:
        print(warps(state))

state.stop()


