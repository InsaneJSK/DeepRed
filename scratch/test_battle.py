"""
scratch/test_battle.py

Tests BattleController against the oak-room-battle save state.

Walk into grass in the SDL2 window to trigger a wild battle.
The script will:
  1. Detect when the battle starts (wIsInBattle != 0).
  2. Wait for the player's turn (wBattleTurnSide == 3).
  3. Print the current RAM state.
  4. Use the FIGHT command (move index 0 — first move).
  5. Wait for the next player turn.
  6. Use RUN to try to escape.
  7. Confirm the battle ended.

Press ESC at any time to quit.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pyboy import PyBoy
from memory_state.game_state import PokemonGameState
from autonomous_controller.battle_controller import BattleController
import keyboard

# ---------------------------------------------------------------------------
ROM        = "Pokemon_Red/Red.gb"
SAVE_STATE = "saves/oak-room-battle.state"
# ---------------------------------------------------------------------------

pyboy = PyBoy(ROM, window="SDL2")
pyboy.tick()
with open(SAVE_STATE, "rb") as f:
    pyboy.load_state(f)

gs = PokemonGameState(pyboy)
bc = BattleController(pyboy, gs)

print("Walk into grass to start a battle. Press ESC to quit.")

# ── Phase 1: wait for battle to start ─────────────────────────────────────
while not bc.is_in_battle():
    pyboy.tick()
    if keyboard.is_pressed("esc"):
        print("Quit.")
        pyboy.stop()
        sys.exit()

print(f"\n[TEST] Battle started! wIsInBattle = {gs.mem.read_byte(0xD057)}")
print(f"       Party: {[p['species_name'] for p in gs.party_pokemon]}")

# ── Phase 2: wait for player's first turn ─────────────────────────────────
print("[TEST] Waiting for player turn (advancing intro text)…")
ok = bc.wait_for_turn(timeout=6000)   # trainer intro can be long
print(f"[TEST] Player turn ready: {ok}")
print(f"       wCurrentMenuItem  = {bc.menu_cursor()}")
print(f"       is_player_turn() = {bc.is_player_turn()}")
print(f"       Dialog snippet   = {repr(gs.dialog[:80])}")

# Print the party's actual move order so we know which index to use
moves = gs.party_pokemon[0].get("moves, pp", [])
print(f"       Moves: {[m[0] for m in moves]}")

# ── Phase 3: use first move (index 0 = top of move list) ──────────────────
print("\n[TEST] Using FIGHT → move 0…")
bc.fight(move_index=1)

print("[TEST] Waiting for next player turn after move…")
ok = bc.wait_for_turn(timeout=6000)
print(f"[TEST] Turn returned: {ok}  |  still in battle: {bc.is_in_battle()}")

# ── Phase 4: run (wild only) ───────────────────────────────────────────────
if bc.is_in_battle():
    if bc.is_wild_battle():
        print("\n[TEST] Wild battle — attempting RUN…")
        bc.run()
        bc.wait_for_turn(timeout=300)
        print(f"[TEST] Escaped: {not bc.is_in_battle()}")
    else:
        print("\n[TEST] Trainer battle — skipping RUN (you can't flee trainers).")
        print("       Waiting for next turn to confirm continued control…")
        ok2 = bc.wait_for_turn(timeout=6000)
        print(f"       Turn ready: {ok2}  |  wBattleTurnSide={gs.mem.read_byte(0xCCD5)}")
else:
    print("[TEST] Battle already over.")

print("Done. Keeping window open — press ESC to quit.")

while not keyboard.is_pressed("esc"):
    pyboy.tick()

pyboy.stop()
