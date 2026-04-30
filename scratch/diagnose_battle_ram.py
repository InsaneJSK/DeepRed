"""
scratch/diagnose_battle_ram.py

RAM-address diagnostic tool for the battle controller redesign.

Usage
-----
    python scratch/diagnose_battle_ram.py

1. The emulator opens at the oak-room-battle save state.
2. Walk into grass to trigger a battle (use arrow keys in the SDL2 window).
3. Once a battle starts, use the battle menu normally.
4. Every 30 ticks the script prints a table of candidate addresses so you can
   confirm which ones track the cursor, input-lock state, etc.
5. Press ESC in the terminal (via keyboard library) to quit.

What to look for
----------------
When you move the cursor in the FIGHT/PKMN/ITEM/RUN menu:
  - wCurrentMenuItem (0xCC26) should change between 0-3
When you open the FIGHT sub-menu:
  - The move-cursor address (0xCC2A candidate) should change 0-3
Between move animations / text scrolling:
  - Input-lock candidates (0xFFAC, 0xC0EE) should be non-zero,
    then drop to 0 when you regain control
When text is printing on screen:
  - Text-print flag candidates (0xC4F1, 0xFF8C) should be non-zero
"""

import sys
import os
import time

# ---------------------------------------------------------------------------
# PyBoy + game state bootstrap
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pyboy import PyBoy
from pyboy.utils import WindowEvent
from memory_state.game_state import PokemonGameState
import keyboard  # pip install keyboard

SAVE_STATE = "saves/oak-room-battle.state"
ROM_PATH   = "Pokemon_Red/Red.gb"

pyboy = PyBoy(ROM_PATH, window="SDL2")
pyboy.tick()
with open(SAVE_STATE, "rb") as f:
    pyboy.load_state(f)

gs = PokemonGameState(pyboy)

# ---------------------------------------------------------------------------
# Addresses under investigation
# ---------------------------------------------------------------------------
CANDIDATES = {
    # ── Confirmed from game_state.py ──────────────────────────────────────
    "in_battle    [0xD057]": 0xD057,

    # ── Battle-menu cursor ────────────────────────────────────────────────
    # wCurrentMenuItem — should be 0=FIGHT 1=PKMN 2=ITEM 3=RUN in main menu
    "menuItem     [0xCC26]": 0xCC26,
    # wCurrentMenuRow — second dimension of the 2-row layout (might be same)
    "menuRow      [0xCC27]": 0xCC27,
    # Possible move-slot cursor during FIGHT sub-menu
    "moveCursor   [0xCC2A]": 0xCC2A,
    # wMenuItemToSwap / wWhichPokemon
    "whichPkmn    [0xCC35]": 0xCC35,

    # ── Input / joypad lock ───────────────────────────────────────────────
    # wJoyIgnore — set while input should be ignored (animation, text)
    "joyIgnore    [0xFFAC]": 0xFFAC,
    # wPrintLetterDelay / general text-busy flag
    "textBusy1    [0xC4F1]": 0xC4F1,
    # Another text-flag candidate
    "textBusy2    [0xFF8C]": 0xFF8C,
    # Game state / sub-state byte (sometimes used as busy flag)
    "gameState    [0xC0EE]": 0xC0EE,

    # ── Battle data ───────────────────────────────────────────────────────
    "enemySpecies [0xCFD8]": 0xCFD8,
    "enemyHPhi    [0xCFE6]": 0xCFE6,
    "enemyHPlo    [0xCFE7]": 0xCFE7,
    # Which party slot is active for the player
    "playerSlot   [0xCC2F]": 0xCC2F,
    # wBattleType: 0=wild, 1=trainer
    "battleType   [0xD05A]": 0xD05A,
    # wIsInBattle duplicate sometimes used for sub-menus
    "battleSub    [0xCCD5]": 0xCCD5,
}

# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------
SEP  = "─" * 56
TICK_INTERVAL = 30   # print every N ticks

def snapshot() -> dict[str, int]:
    return {label: gs.mem.read_byte(addr) for label, addr in CANDIDATES.items()}


def print_snapshot(snap: dict[str, int], tick: int) -> None:
    print(f"\n{SEP}")
    print(f"  Tick {tick:>6}  |  dialog: {repr(gs.dialog[:60])}")
    print(SEP)
    for label, val in snap.items():
        bar = "█" * val if val <= 32 else f"0x{val:02X}"
        print(f"  {label:<30}  {val:>4}  {bar}")
    print(SEP)

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
print("=" * 56)
print("  Battle RAM Diagnostic")
print("  Walk into grass to start a battle, then use the menu.")
print("  Press ESC to quit.")
print("=" * 56)

tick = 0
last_snap: dict[str, int] = {}

try:
    while not keyboard.is_pressed("esc"):
        pyboy.tick()
        tick += 1

        if tick % TICK_INTERVAL == 0:
            snap = snapshot()

            # Only print when something changed (reduces noise)
            if snap != last_snap:
                print_snapshot(snap, tick)
                last_snap = snap.copy()

except KeyboardInterrupt:
    pass

print("\nQuitting…")
pyboy.stop()
