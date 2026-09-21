"""Run a local Ollama model against the same actions used by the human demo."""

import argparse
import json
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

from pyboy.utils import WindowEvent

from autonomous_controller.agent_interface import AgentInterface
from autonomous_controller.emulator_session import open_emulator
from autonomous_controller.game_data import validate_rom
from autonomous_controller.llm_runner import PLAY_INSTRUCTION, OllamaClient, run_agent
from autonomous_controller.presentation import DecisionWindow, PresentedSession
from demo import ROOT, save_checkpoint
from memory_state.game_state import PokemonGameState

import time
def paused_request(session, perform):
    """HTTP runs in a daemon worker; SDL and emulation stay on the main thread."""
    results = Queue()

    def worker():
        try:
            results.put((True, perform()))
        except BaseException as error:
            results.put((False, error))

    session.send_input(WindowEvent.PAUSE)
    session.tick()
    Thread(target=worker, daemon=True).start()
    try:
        while True:
            try:
                success, value = results.get(timeout=0.02)
                if not success:
                    raise value
                return value
            except Empty:
                session.tick()
    finally:
        session.send_input(WindowEvent.UNPAUSE)


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be at least 1")
    return number


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    stops = parser.add_mutually_exclusive_group()
    stops.add_argument(
        "--stop-at",
        type=str.upper,
        default="VIRIDIAN_CITY",
        help="Local stop on arrival (default VIRIDIAN_CITY); never sent to the model",
    )
    stops.add_argument(
        "--no-stop-at",
        dest="stop_at",
        action="store_const",
        const=None,
        help="Disable the local arrival stop",
    )
    parser.add_argument("--model", default="gemma3:4b")
    parser.add_argument("--url", default="http://localhost:11434")
    parser.add_argument("--save", type=Path, default=ROOT / "saves/in-room-start.state")
    parser.add_argument("--rom", type=Path, default=ROOT / "Pokemon_Red/Red.gb")
    parser.add_argument("--max-calls", type=positive_int, default=30)
    parser.add_argument("--max-actions", type=positive_int, default=200)
    parser.add_argument("--timeout", type=positive_int, default=180)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--overlay",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show the game and a decision panel in one window",
    )
    parser.add_argument("--verbose", action="store_true", help="Print controller diagnostics")
    parser.add_argument("--auto-resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--auto-flee",
        action="store_true",
        help="Attempt escape automatically up to twice per wild encounter (default off)",
    )
    parser.add_argument(
        "--think",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable reasoning for supported Ollama models (slower)",
    )
    return parser


def main(argv=None):
    never_before = False
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_rom(args.rom)
    client = OllamaClient(args.model, args.url, args.timeout, args.think)
    directory = ROOT / "status/llm-runs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    with args.save.open("rb") as saved:
        directory.mkdir(parents=True)
        print(f"DeepRed | {args.model} | {PLAY_INSTRUCTION}", flush=True)
        with open_emulator(
            args.rom,
            window="null" if args.headless or args.overlay else "SDL2",
            sound_emulated=False,
        ) as session:
            session.set_emulation_speed(0 if args.headless else 1)
            session.load_state(saved)
            session.tick()
            window = None
            checkpoints = 0

            def checkpoint():
                nonlocal checkpoints
                checkpoints += 1
                path = save_checkpoint(session, directory / f"checkpoint-{checkpoints:04d}.state")
                if args.verbose:
                    print("Checkpoint:", path, flush=True)

            try:
                if not args.headless and args.overlay:
                    window = DecisionWindow(session, PLAY_INSTRUCTION, args.model, args.max_calls)
                    session = PresentedSession(session, window)
                    if not never_before:
                        time.sleep(10)
                        never_before = True
                agent = AgentInterface(session, PokemonGameState(session))
                if args.stop_at and agent.navigation.graph.map_id(args.stop_at) is None:
                    parser.error(f"Unknown --stop-at map: {args.stop_at}")

                def request(perform):
                    if window:
                        window.status = "Thinking"
                        window.pump(force=True)
                    return paused_request(session, perform)

                with (
                    (directory / "events.jsonl").open("w", encoding="utf-8") as log,
                    (directory / "controller.log").open("w", encoding="utf-8") as diagnostics,
                ):
                    try:
                        stats = run_agent(
                            agent,
                            client,
                            log,
                            max_calls=args.max_calls,
                            max_actions=args.max_actions,
                            auto_resume=args.auto_resume,
                            auto_flee=args.auto_flee,
                            stop_at=args.stop_at,
                            request=None if args.headless else request,
                            checkpoint=checkpoint,
                            on_decision=window.choose if window else None,
                            on_automatic=window.automatic if window else None,
                            diagnostics=diagnostics,
                            verbose=args.verbose,
                        )
                    finally:
                        checkpoint()
                    (directory / "summary.json").write_text(
                        json.dumps(stats, indent=2), encoding="utf-8"
                    )
                print("Saved run:", directory, flush=True)
                if window:
                    window.hold(stats)
            finally:
                if window:
                    window.close()


if __name__ == "__main__":
    main()
