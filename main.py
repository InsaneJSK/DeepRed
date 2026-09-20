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
from autonomous_controller.emulator_session import EmulatorClosed, EmulatorSession
from autonomous_controller.game_data import validate_rom
from memory_state.game_state import PokemonGameState

# CONFIG
ROM = "Pokemon_Red/Red.gb"
SAVE_STATE = "saves/in-room-start.state"
STARTER = "charmander"  # "bulbasaur" | "charmander" | "squirtle"
GOAL = "VIRIDIAN_CITY"
MAX_TURNS = 50  # safety cap per battle
MAX_STALLED_INTERRUPTS = 8  # interruptions returning to the same map/position


# Battle loop (called whenever a battle is detected)
def _run_battle_loop(bc: BattleController, gs: PokemonGameState) -> None:
    """Flee wild encounters during travel; fight required trainer battles."""
    b_type = {1: "WILD", 2: "TRAINER"}.get(gs.mem.read_byte(0xD057), "BATTLE")
    print(f"\n[BATTLE] {b_type} started!")
    if gs.party_pokemon:
        moves = gs.party_pokemon[0].get("moves, pp", [])
        print(
            f"[BATTLE] Lead: {gs.party_pokemon[0].get('species_name', '?')}  "
            f"Moves: {[m[0] for m in moves]}"
        )

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
        if bc.needs_switch():
            reserves = bc.available_switches()
            if not reserves or not bc.switch(reserves[0]):
                print("[BATTLE] Could not send out a healthy replacement.")
                break
            continue
        if bc.is_wild_battle():
            print(f"[BATTLE] Turn {turn} — attempting escape")
            accepted = bc.run()
        else:
            print(f"[BATTLE] Turn {turn} — fight(move_index=0)")
            accepted = bc.fight(move_index=0)
        if not accepted:
            print("[BATTLE] Action did not reach the expected menu; stopping.")
            break

    if bc.is_in_battle():
        print("[BATTLE] Handler stopped before the battle finished.")
        return
    # Advance XP-gain / level-up text until back in the overworld
    print("[BATTLE] Clearing post-battle text…")
    if not bc.clear_post_battle_text(timeout=3000):
        raise TimeoutError("Post-battle dialogue did not clear within its frame budget")
    print(f"[BATTLE] Done. Still in battle: {bc.is_in_battle()}")


def main() -> None:
    validate_rom(ROM)
    pyboy = PyBoy(ROM, window="SDL2")
    try:
        _run_agent(EmulatorSession(pyboy))
    except (EmulatorClosed, KeyboardInterrupt):
        print("\n[AGENT] Emulator closed. Exiting.")
    finally:
        pyboy.stop(save=False)


def _run_agent(pyboy) -> None:
    pyboy.tick()
    with open(SAVE_STATE, "rb") as f:
        pyboy.load_state(f)

    gs = PokemonGameState(pyboy)
    controller = AutonomousController(pyboy, gs, starter=STARTER)
    bc = BattleController(pyboy, gs)

    print("=" * 60)
    print("  DeepRed — Autonomous Pokemon Red Agent")
    print(f"  Goal: {GOAL}  |  Starter: {STARTER}")
    print("=" * 60)

    stalled = 0
    while True:
        # Handle any battle in progress before navigating
        if bc.is_in_battle():
            _run_battle_loop(bc, gs)
            if bc.is_in_battle():
                print("[AGENT] Battle handler could not finish; stopping.")
                break
            continue

        before = (gs.map["map_id"], gs.map["player_x"], gs.map["player_y"])
        print(f"\n[AGENT] go_to({GOAL})…")
        try:
            ok = controller.go_to(GOAL)
        except BattleInterrupt:
            print("[AGENT] BattleInterrupt mid-navigation.")
            _run_battle_loop(bc, gs)
            if bc.is_in_battle():
                print("[AGENT] Battle handler could not finish; stopping.")
                break
            after = (gs.map["map_id"], gs.map["player_x"], gs.map["player_y"])
            stalled = stalled + 1 if after == before else 0
            if stalled >= MAX_STALLED_INTERRUPTS:
                print("[AGENT] Repeated interruptions without travel progress; stopping.")
                break
            continue

        if ok:
            print(f"\n[AGENT] ✓ Reached {GOAL}!")
            break

        print(f"[AGENT] Navigation stopped: {controller.last_error}")
        break

    print("\nClose the emulator window or press Ctrl+C in the terminal to exit.")
    while pyboy.tick():
        pass


if __name__ == "__main__":
    main()
