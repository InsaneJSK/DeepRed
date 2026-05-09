"""
main.py

DeepRed — autonomous Pokemon Red agent.

The agent loop is intentionally minimal:
  - BattleController handles combat whenever wIsInBattle is set.
  - AutonomousController.go_to() handles navigation, including the
    Oak's-lab / starter-picking fallback automatically.

To change the starter or goal, edit the CONFIG block below.
"""

from pyboy import PyBoy
from autonomous_controller import AutonomousController, BattleController, BattleInterrupt
from memory_state.game_state import PokemonGameState

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
ROM        = "Pokemon_Red/Red.gb"
SAVE_STATE = "saves/in-room-start.state"
GRAPH      = "world_graph.json"
STARTER    = "charmander"   # "bulbasaur" | "charmander" | "squirtle"
GOAL       = "ROUTE_1"
MAX_TURNS  = 50             # safety cap per battle


# ---------------------------------------------------------------------------
# Battle loop (called whenever a battle is detected)
# ---------------------------------------------------------------------------

def _run_battle_loop(bc: BattleController, gs: PokemonGameState) -> None:
    """Fight every turn with move 0 until the battle ends."""
    b_type = {1: "WILD", 2: "TRAINER"}.get(gs.mem.read_byte(0xD057), "BATTLE")
    print(f"\n[BATTLE] {b_type} started!")
    if gs.party_pokemon:
        moves = gs.party_pokemon[0].get("moves, pp", [])
        print(f"[BATTLE] Lead: {gs.party_pokemon[0].get('species_name','?')}  "
              f"Moves: {[m[0] for m in moves]}")

    for turn in range(1, MAX_TURNS + 1):
        if not bc.is_in_battle():
            break
        print(f"[BATTLE] Turn {turn} — waiting for menu…")
        ready = bc.wait_for_turn(timeout=6000)
        if not ready:
            if not bc.is_in_battle():
                print("[BATTLE] Battle ended mid-wait.")
            else:
                print("[BATTLE] Timed out waiting for menu — aborting.")
            break
        print(f"[BATTLE] Turn {turn} — fight(move_index=0)")
        bc.fight(move_index=0)

    # Advance XP-gain / level-up text until back in the overworld
    print("[BATTLE] Clearing post-battle text…")
    bc.clear_post_battle_text(timeout=3000)
    print(f"[BATTLE] Done. Still in battle: {bc.is_in_battle()}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    pyboy = PyBoy(ROM, window="SDL2")
    pyboy.tick()
    with open(SAVE_STATE, "rb") as f:
        pyboy.load_state(f)

    gs         = PokemonGameState(pyboy)
    controller = AutonomousController(pyboy, gs, GRAPH, starter=STARTER)
    bc         = BattleController(pyboy, gs)

    print("=" * 60)
    print("  DeepRed — Autonomous Pokemon Red Agent")
    print(f"  Goal: {GOAL}  |  Starter: {STARTER}")
    print("=" * 60)

    MAX_RETRIES = 8
    for attempt in range(MAX_RETRIES):
        # Handle any battle in progress before navigating
        if bc.is_in_battle():
            _run_battle_loop(bc, gs)
            continue

        print(f"\n[AGENT] Attempt {attempt + 1} — go_to({GOAL})…")
        try:
            ok = controller.go_to(GOAL)
        except BattleInterrupt:
            print("[AGENT] BattleInterrupt mid-navigation.")
            _run_battle_loop(bc, gs)
            continue

        if ok:
            print(f"\n[AGENT] ✓ Reached {GOAL}!")
            break

        print(f"[AGENT] go_to returned False — map: {gs.to_dict().get('map_name')}")

    else:
        print(f"\n[AGENT] ✗ Failed to reach {GOAL} after {MAX_RETRIES} attempts.")

    print("\nKeep window open. Close it to exit.")
    while True:
        pyboy.tick()


if __name__ == "__main__":
    main()
