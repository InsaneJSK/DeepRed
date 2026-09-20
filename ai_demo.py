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
from autonomous_controller.llm_runner import OllamaClient, run_agent
from demo import ROOT, save_checkpoint
from memory_state.game_state import PokemonGameState


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goal", required=True)
    parser.add_argument(
        "--stop-at",
        type=str.upper,
        help="Stop automatically when this map is reached outside battle/dialogue",
    )
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--url", default="http://localhost:11434")
    parser.add_argument("--save", type=Path, default=ROOT / "saves/in-room-start.state")
    parser.add_argument("--rom", type=Path, default=ROOT / "Pokemon_Red/Red.gb")
    parser.add_argument("--max-calls", type=positive_int, default=30)
    parser.add_argument("--max-actions", type=positive_int, default=200)
    parser.add_argument("--timeout", type=positive_int, default=180)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--auto-resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--think",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable reasoning for models that support it (slower)",
    )
    args = parser.parse_args(argv)
    validate_rom(args.rom)
    client = OllamaClient(args.model, args.url, args.timeout, args.think)
    directory = ROOT / "status/llm-runs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    with args.save.open("rb") as saved:
        directory.mkdir(parents=True)
        print("Run files:", directory, flush=True)
        with open_emulator(
            args.rom, window="null" if args.headless else "SDL2", sound_emulated=False
        ) as session:
            session.set_emulation_speed(0 if args.headless else 1)
            session.load_state(saved)
            session.tick()
            agent = AgentInterface(session, PokemonGameState(session))
            if args.stop_at and agent.navigation.graph.map_id(args.stop_at) is None:
                parser.error(f"Unknown --stop-at map: {args.stop_at}")
            checkpoints = 0

            def checkpoint():
                nonlocal checkpoints
                checkpoints += 1
                path = save_checkpoint(session, directory / f"checkpoint-{checkpoints:04d}.state")
                print("Checkpoint:", path, flush=True)

            try:
                with (directory / "events.jsonl").open("w", encoding="utf-8") as log:
                    stats = run_agent(
                        agent,
                        client,
                        args.goal,
                        log,
                        max_calls=args.max_calls,
                        max_actions=args.max_actions,
                        auto_resume=args.auto_resume,
                        stop_at=args.stop_at,
                        request=None if args.headless else lambda fn: paused_request(session, fn),
                        checkpoint=checkpoint,
                    )
                    (directory / "summary.json").write_text(
                        json.dumps(stats, indent=2), encoding="utf-8"
                    )
            finally:
                checkpoint()


if __name__ == "__main__":
    main()
