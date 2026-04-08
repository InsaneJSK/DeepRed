"""
autonomous_controller/constants.py

Shared constants: direction vocabulary, RAM addresses, compass mapping.
"""

from pyboy.utils import WindowEvent  # pylint: disable=no-name-in-module

# ---------------------------------------------------------------------------
# RAM addresses
# ---------------------------------------------------------------------------
ADDR_WARP_COUNT = 0xD3AE        # Number of warps on current map
ADDR_WARP_BASE  = 0xD3AF        # Warp table: 4 bytes each (y, x, dest_warp_idx, dest_map_id)
ADDR_MAP_ID     = 0xD35E        # Current map ID

# ---------------------------------------------------------------------------
# Direction vocabulary — up/down/left/right only, no compass
# ---------------------------------------------------------------------------
# dx, dy, press event, release event
DIRECTIONS: dict[str, tuple[int, int, int, int]] = {
    "up":    ( 0, -1, WindowEvent.PRESS_ARROW_UP,    WindowEvent.RELEASE_ARROW_UP),
    "down":  ( 0,  1, WindowEvent.PRESS_ARROW_DOWN,  WindowEvent.RELEASE_ARROW_DOWN),
    "left":  (-1,  0, WindowEvent.PRESS_ARROW_LEFT,  WindowEvent.RELEASE_ARROW_LEFT),
    "right": ( 1,  0, WindowEvent.PRESS_ARROW_RIGHT, WindowEvent.RELEASE_ARROW_RIGHT),
}

OPPOSITE: dict[str, str] = {
    "up": "down", "down": "up",
    "left": "right", "right": "left",
}

# pokered connection headers use compass — translate once here
COMPASS_TO_ARROW: dict[str, str] = {
    "north": "up",
    "south": "down",
    "west":  "left",
    "east":  "right",
}
