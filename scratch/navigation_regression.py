"""Headless integration check using real gameplay; original saves remain untouched."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pyboy import PyBoy

from autonomous_controller import AutonomousController, BattleController, BattleInterrupt
from autonomous_controller.game_data import validate_rom
from main import _run_battle_loop
from memory_state.game_state import PokemonGameState


class FrameBudget:
    def __init__(self, game, limit=200000):
        self.game, self.limit, self.frames = game, limit, 0

    def __getattr__(self, name):
        return getattr(self.game, name)

    def tick(self, count=1, *args, **kwargs):
        self.frames += count
        if self.frames > self.limit:
            raise TimeoutError("Emulator frame budget exceeded")
        return self.game.tick(count, *args, **kwargs)


def verify_expected_block(controller, map_name, position, reason):
    actual = (controller._map_name(), controller._pos(), controller.last_error)
    expected = (map_name.upper(), tuple(position), reason)
    if actual != expected:
        raise AssertionError(f"Wrong blockage: expected {expected!r}, got {actual!r}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="saves/in-room-start.state")
    parser.add_argument("--goals", nargs="+", default=["ROUTE_1", "VIRIDIAN_CITY"])
    parser.add_argument("--checkpoint", action="store_true")
    parser.add_argument("--expect-blocked", action="store_true")
    parser.add_argument("--blocked-map")
    parser.add_argument("--blocked-position", type=int, nargs=2, metavar=("X", "Y"))
    parser.add_argument("--blocked-reason")
    args = parser.parse_args()
    if args.expect_blocked and not all(
        [args.blocked_map, args.blocked_position is not None, args.blocked_reason]
    ):
        parser.error(
            "--expect-blocked requires --blocked-map, --blocked-position and --blocked-reason"
        )
    validate_rom(ROOT / "Pokemon_Red/Red.gb")
    game = PyBoy(str(ROOT / "Pokemon_Red/Red.gb"), window="null", sound_emulated=False)
    game.set_emulation_speed(0)
    p = FrameBudget(game)
    try:
        with open(ROOT / args.state, "rb") as f:
            p.load_state(f)
        p.tick()
        gs = PokemonGameState(p)
        controller = AutonomousController(p, gs)
        battle = BattleController(p, gs)
        for goal in args.goals:
            arrived = False
            for attempt in range(12):
                if battle.is_in_battle():
                    _run_battle_loop(battle, gs)
                    if battle.is_in_battle():
                        raise RuntimeError("Battle handler did not finish battle")
                try:
                    arrived = controller.go_to(goal)
                except BattleInterrupt:
                    arrived = False
                print(
                    "ATTEMPT", goal, attempt, json.dumps(gs.map), controller.nav_stats, flush=True
                )
                if not arrived and not battle.is_in_battle():
                    if args.expect_blocked:
                        verify_expected_block(
                            controller, args.blocked_map, args.blocked_position, args.blocked_reason
                        )
                        print("EXPECTED_BLOCK", controller.last_error, flush=True)
                        return
                    raise AssertionError(f"Navigation blocked: {controller.last_error}")
                if arrived:
                    break
            if not arrived:
                p.screen.image.save(str(ROOT / "scratch/navigation_failure.png"))
                raise AssertionError(f"Failed to reach {goal}")
            print("REACHED", goal, "frames", p.frames, "stats", controller.nav_stats, flush=True)
            if args.expect_blocked:
                raise AssertionError(f"Expected a story gate before {goal}")
            if args.checkpoint:
                with open(ROOT / f"scratch/reached_{goal}.state", "wb") as f:
                    p.save_state(f)
        print("PASS", args.goals, flush=True)
    finally:
        p.stop(save=False)


if __name__ == "__main__":
    main()
