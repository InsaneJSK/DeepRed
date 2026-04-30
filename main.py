"""
main.py

DeepRed — autonomous Pokemon Red agent.

State machine
-------------
  PHASE 0 — "Need starter"
      Navigate until Oak's cutscene fires (go_to returns False in OAKS_LAB),
      then call pick_starter().  A rival battle may immediately follow; the
      battle loop handles it.

  PHASE 1 — "Navigate to Route 1"
      go_to("ROUTE_1").  Any wild or trainer battle mid-route is intercepted
      by the BattleController.

The same _run_battle_loop() handles all battles (wild, trainer, rival).
To extend the battle strategy, replace the logic inside that function.
"""

from pyboy import PyBoy
from autonomous_controller import AutonomousController, BattleController, BattleInterrupt
from memory_state.game_state import PokemonGameState

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ROM        = "Pokemon_Red/Red.gb"
SAVE_STATE = "saves/in-room-start.state"
GRAPH      = "world_graph.json"
STARTER    = "charmander"   # "bulbasaur" | "charmander" | "squirtle"
GOAL       = "ROUTE_1"

MAX_NAV_RETRIES    = 8    # retries for a single go_to() leg
MAX_BATTLE_TURNS   = 50   # safety cap per battle


# ---------------------------------------------------------------------------
# Battle loop
# ---------------------------------------------------------------------------

def _run_battle_loop(bc: BattleController, gs: PokemonGameState) -> None:
    """Fight every turn with move 0 until the battle ends."""
    b_type = {0: "none", 1: "wild", 2: "trainer"}.get(gs.mem.read_byte(0xD057), "?")
    print(f"\n[BATTLE] {b_type.upper()} battle detected!")

    if gs.party_pokemon:
        moves = gs.party_pokemon[0].get("moves, pp", [])
        lead  = gs.party_pokemon[0].get("species_name", "?")
        print(f"[BATTLE] Lead: {lead}  Moves: {[m[0] for m in moves]}")

    for turn in range(1, MAX_BATTLE_TURNS + 1):
        if not bc.is_in_battle():
            break

        print(f"[BATTLE] Turn {turn} — waiting for menu…")
        if not bc.wait_for_turn(timeout=6000):
            print("[BATTLE] Timed out waiting for turn — aborting.")
            break

        print(f"[BATTLE] Turn {turn} — fight(move_index=0)")
        bc.fight(move_index=0)

    # Clear any post-battle text (XP gain, level-up, etc.)
    bc.wait_for_turn(timeout=3000)
    print(f"[BATTLE] Battle resolved. Still in battle: {bc.is_in_battle()}")


# ---------------------------------------------------------------------------
# Phase 0 — acquire starter
# ---------------------------------------------------------------------------

def _phase_starter(controller: AutonomousController,
                   bc: BattleController,
                   gs: PokemonGameState) -> bool:
    """
    Navigate until Oak's cutscene fires, then pick the starter.

    Returns True once a Pokemon is in the party.
    Returns False if we exceeded retries without getting a starter.
    """
    print("\n[PHASE 0] No starter yet — navigating to trigger Oak's event…")

    for attempt in range(MAX_NAV_RETRIES):
        # Battle might fire before or after Oak's cutscene (rival battle)
        if bc.is_in_battle():
            _run_battle_loop(bc, gs)

        # If party is now non-empty (rival battle gave us the starter somehow?),
        # or pick_starter already ran successfully — we're done.
        if gs.party_pokemon:
            print(f"[PHASE 0] Starter acquired: {gs.party_pokemon[0].get('species_name','?')}")
            return True

        current = gs.to_dict().get("map_name", "UNKNOWN")
        print(f"[PHASE 0] Attempt {attempt+1}: map={current}")

        if current == "OAKS_LAB":
            # Oak's cutscene has ended and we regained control — pick the starter.
            print(f"[PHASE 0] In Oak's lab — picking {STARTER}…")
            ok = controller.pick_starter(STARTER)
            if ok:
                print("[PHASE 0] Starter picked!")
                # Rival battle may fire immediately — handled at top of next loop
                continue
            else:
                print("[PHASE 0] pick_starter() timed out — retrying.")
                continue

        # Navigate toward Route 1 (passes through Pallet Town, triggering Oak)
        try:
            controller.go_to("ROUTE_1")
        except BattleInterrupt:
            _run_battle_loop(bc, gs)
            continue
        # go_to returned False (Oak cutscene interrupted navigation) or True
        # (reached Route 1 somehow before needing a starter — unlikely but safe)

    print(f"[PHASE 0] Failed to acquire starter after {MAX_NAV_RETRIES} attempts.")
    return False


# ---------------------------------------------------------------------------
# Phase 1 — navigate to Route 1
# ---------------------------------------------------------------------------

def _phase_navigate(controller: AutonomousController,
                    bc: BattleController,
                    gs: PokemonGameState) -> bool:
    """
    Navigate from the current position to ROUTE_1.

    Returns True on success, False after exhausting retries.
    """
    print(f"\n[PHASE 1] Navigating to {GOAL}…")

    for attempt in range(MAX_NAV_RETRIES):
        if bc.is_in_battle():
            _run_battle_loop(bc, gs)

        current = gs.to_dict().get("map_name", "UNKNOWN")
        print(f"[PHASE 1] Attempt {attempt+1}: map={current}")

        if current == GOAL:
            print(f"[PHASE 1] ✓ Arrived at {GOAL}!")
            return True

        try:
            ok = controller.go_to(GOAL)
        except BattleInterrupt:
            print("[PHASE 1] BattleInterrupt mid-navigation — handling…")
            _run_battle_loop(bc, gs)
            continue

        if ok:
            print(f"[PHASE 1] ✓ Arrived at {GOAL}!")
            return True

        # go_to() returned False — check common causes
        current = gs.to_dict().get("map_name", "UNKNOWN")
        print(f"[PHASE 1] go_to failed. Now at: {current}")

        if bc.is_in_battle():
            continue   # battle loop at top of next iteration

        # Unexpected Oak's lab (trainer locked us) — try again
        if current == "OAKS_LAB" and not gs.party_pokemon:
            ok = controller.pick_starter(STARTER)
            if ok:
                continue

    print(f"[PHASE 1] Failed to reach {GOAL} after {MAX_NAV_RETRIES} attempts.")
    return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # ── Boot ─────────────────────────────────────────────────────────────────
    pyboy = PyBoy(ROM, window="SDL2")
    pyboy.tick()
    with open(SAVE_STATE, "rb") as f:
        pyboy.load_state(f)

    gs         = PokemonGameState(pyboy)
    controller = AutonomousController(pyboy, gs, GRAPH)
    bc         = BattleController(pyboy, gs)

    print("=" * 60)
    print("  DeepRed — Autonomous Pokemon Red Agent")
    print(f"  Save: {SAVE_STATE}  |  Goal: {GOAL}")
    print("=" * 60)

    # Handle any battle that might be in progress on load
    if bc.is_in_battle():
        _run_battle_loop(bc, gs)

    # Phase 0: get starter if we don't have one
    if not gs.party_pokemon:
        ok = _phase_starter(controller, bc, gs)
        if not ok:
            print("[MAIN] Could not acquire starter — exiting.")
            pyboy.stop()
            return

    # Phase 1: navigate to Route 1
    ok = _phase_navigate(controller, bc, gs)

    if ok:
        print(f"\n[MAIN] ✓ Goal reached: {GOAL}")
    else:
        print(f"\n[MAIN] ✗ Could not reach {GOAL}.")

    print("\nKeep window open. Close it to exit.")
    while True:
        pyboy.tick()


if __name__ == "__main__":
    main()
