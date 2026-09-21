"""Human-controlled demo of the same action interface used by an AI."""

import argparse
import json
from datetime import datetime
from io import BytesIO
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

from pyboy.utils import WindowEvent

from autonomous_controller.agent_interface import AgentInterface
from autonomous_controller.emulator_session import open_emulator
from autonomous_controller.game_data import validate_rom
from memory_state.game_state import PokemonGameState

ROOT = Path(__file__).resolve().parent
HELP = """Commands (party/bag/move indices start at 0; use object slots as printed):
  navigate MAP_NAME       Travel across maps to a destination, e.g. ROUTE_1
  interact SLOT           Approach and talk to a listed object, e.g. interact 1
  choose_starter NAME      Choose bulbasaur, charmander, or squirtle at Oak's lab
  choose OPTION           Choose a displayed menu option (indices start at 0)
  quantity AMOUNT         Set the shop quantity, e.g. quantity 5
  resume                  Resume travel interrupted by a battle
  fight MOVE              Select a move, e.g. fight 0
  switch PARTY            Send out a healthy reserve, e.g. switch 1
  use_item BAG [PARTY [MOVE]]   Ball: use_item 0; Potion: use_item 2 0
  run                     Attempt to flee
  cancel                  Back out of an optional menu
  advance                 Advance dialogue/animation to the next decision
  observe                 Show the current summary again
  json                    Show the complete observation
  /save [PATH]            Save a checkpoint (default: timestamped file in saves/)
  help                    Show this help
  quit                    Exit without saving

The game is paused while you type. Only listed available actions can execute.
After fight/run, use advance as needed. Exit access may be blocked by the story.
"""


def parse_command(line):
    """Translate human syntax into the interface's existing action contract."""
    words = line.split()
    if not words:
        return None
    action = words[0].lower()
    for schema in AgentInterface.action_schema()["oneOf"]:
        if schema["properties"]["action"]["const"] != action:
            continue
        fields = [key for key in schema["properties"] if key != "action"]
        if not len(schema["required"]) - 1 <= len(words) - 1 <= len(fields):
            raise ValueError("Wrong number of arguments; type help for examples")
        request = {"action": action}
        for field, value in zip(fields, words[1:]):
            if schema["properties"][field]["type"] == "integer":
                try:
                    value = int(value)
                except ValueError:
                    raise ValueError(f"{field} must be a nonnegative integer") from None
                if value < 0:
                    raise ValueError(f"{field} must be a nonnegative integer")
            request[field] = value
        return request
    raise ValueError("Unknown command; type help")


def show(observation):
    location = observation["location"]
    print(
        f"\n{location['map_key']} ({location['player_x']}, {location['player_y']})"
        f" | Decision: {observation['decision']}"
    )
    destinations = sorted({d for exit in observation["exits"] for d in exit["destinations"]})
    print("Exits (access unverified):", ", ".join(destinations) or "none known")
    for obj in observation["objects"]:
        visibility = "visible" if obj["visible"] else "off-screen or hidden"
        print(
            f"Object {obj['slot']}: {obj.get('name', 'Unknown object')} at ({obj['x']}, {obj['y']})"
            f" | {visibility}"
        )
    if observation["pending_destination"]:
        print("Pending travel:", observation["pending_destination"])
    for member in observation["party"]:
        moves = ", ".join(f"{m['index']}:{m['name']} ({m['pp']} PP)" for m in member["moves"])
        print(
            f"Party {member['index']}: {member['nickname']} L{member['level']} "
            f"HP {member['hp']}/{member['max_hp']} {member['status']} | {moves}"
        )
    print(
        "Bag:",
        ", ".join(f"{i['index']}:{i['name']} x{i['quantity']}" for i in observation["inventory"])
        or "empty",
    )
    menu = observation.get("menu")
    if observation.get("starter_options"):
        print("Starters:", ", ".join(observation["starter_options"]))
    if menu:
        print("Menu:", menu["kind"])
        for option in menu["options"]:
            print(f"  {option['index']}: {option['name']}")
        if menu["kind"] == "quantity":
            print(f"Quantity: {menu['current']} (1-{menu['maximum']})")
    battle = observation["battle"]
    if battle["active"]:
        print(
            f"Battle: {battle['kind']} | Menu: {battle['menu']} "
            f"| Active party slot: {battle['active_party_index']}"
        )
        for label in ("player", "opponent"):
            mon = battle[label]
            print(
                f"{label.title()}: {mon['species']} L{mon['level']} "
                f"HP {mon['hp']}/{mon['max_hp']} {mon['status']}"
            )
        print(
            "Live moves:",
            ", ".join(
                f"{m['index']}:{m['name']} ({m['pp']} PP)" for m in battle["player"]["moves"]
            ),
        )
    if observation["dialogue"].strip():
        print("Text:", observation["dialogue"])
    print("Available actions:", ", ".join(observation["actions"]) or "none supported")


def advance_to_choice(agent, observation):
    """Optional policy usable by a human or model runner; never choose a game action."""
    for _ in range(8):
        if observation["actions"] != ["advance"]:
            break
        result = agent.execute({"action": "advance"})
        print(
            f"Auto-advance: {result['status']}"
            + (f" — {result['detail']}" if result["detail"] else "")
        )
        observation = result["observation"]
        if result["status"] != "completed":
            break
    else:
        print("Auto-advance limit reached; returning control to you.")
    return observation


def window_input(session, prompt):
    """Keep SDL events responsive while terminal input waits and gameplay is paused."""
    responses = Queue()

    def read():
        try:
            responses.put(input(prompt))
        except BaseException as error:
            responses.put(error)

    session.send_input(WindowEvent.PAUSE)
    session.tick()
    Thread(target=read, daemon=True).start()
    try:
        while True:
            try:
                result = responses.get(timeout=0.02)
            except Empty:
                session.tick()  # paused ticks pump events, including window close
                continue
            if isinstance(result, BaseException):
                raise result
            return result
    finally:
        session.send_input(WindowEvent.UNPAUSE)


def save_checkpoint(session, destination=None):
    """Save without advancing frames or overwriting an existing checkpoint."""
    path = (
        Path(destination).expanduser()
        if destination
        else ROOT / "saves" / f"demo-{datetime.now():%Y%m%d-%H%M%S-%f}.state"
    ).resolve()
    # Finish serialization before creating a file, so emulator errors leave no file.
    buffer = BytesIO()
    session.save_state(buffer)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        try:
            stream.write(buffer.getvalue())
        except BaseException:
            stream.close()
            path.unlink(missing_ok=True)
            raise
    return path


def run_cli(agent, read=input, *, auto_advance=False, save=None):
    """Human choices and optional automatic waits all go through execute()."""
    print(HELP)
    print(f"Auto-advance: {'on' if auto_advance else 'off'}")
    observation = agent.observe()
    show(advance_to_choice(agent, observation) if auto_advance else observation)
    while True:
        try:
            line = read("\ndeepred> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting without saving.")
            return
        if line.lower() in ("quit", "exit"):
            return
        if line.lower() == "help":
            print(HELP)
        elif line.split(maxsplit=1)[:1] == ["/save"]:
            if save is None:
                print("Saving is unavailable in this session.")
                continue
            parts = line.split(maxsplit=1)
            destination = parts[1].strip().strip('"') if len(parts) == 2 else None
            try:
                path = save(destination)
            except FileExistsError:
                print("That checkpoint already exists; choose another filename.")
            except (OSError, ValueError) as error:
                print(f"Could not save checkpoint: {error}")
            else:
                print(f"Saved checkpoint: {path}")
                print(f'Reopen: uv run python demo.py --save "{path}"')
        elif line.lower() == "observe":
            show(agent.observe())
        elif line.lower() == "json":
            print(json.dumps(agent.observe(), indent=2, ensure_ascii=False))
        else:
            try:
                request = parse_command(line)
            except ValueError as error:
                print(f"Invalid command: {error}")
                continue
            if request is None:
                continue
            result = agent.execute(request)
            print(
                f"Result: {result['status']}"
                + (f" — {result['detail']}" if result["detail"] else "")
            )
            observation = result["observation"]
            if auto_advance and result["status"] in ("completed", "submitted", "interrupted"):
                observation = advance_to_choice(agent, observation)
            show(observation)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, default=ROOT / "Pokemon_Red/Red.gb")
    parser.add_argument(
        "--save",
        type=Path,
        default=ROOT / "saves/in-room-start.state",
        help="Load an emulator checkpoint, including one created with /save",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run without a game window (default); --no-headless shows it",
    )
    parser.add_argument(
        "--auto-advance",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Advance automatically when advance is the only available action",
    )
    args = parser.parse_args(argv)
    validate_rom(args.rom)
    # Open the save first so a missing file fails before emulator creation.
    with args.save.open("rb") as stream:
        with open_emulator(
            args.rom, window="null" if args.headless else "SDL2", sound_emulated=False
        ) as session:
            session.set_emulation_speed(0 if args.headless else 1)
            session.load_state(stream)
            session.tick()
            read = input if args.headless else lambda prompt: window_input(session, prompt)
            run_cli(
                AgentInterface(session, PokemonGameState(session)),
                read=read,
                auto_advance=args.auto_advance,
                save=lambda destination: save_checkpoint(session, destination),
            )


if __name__ == "__main__":
    main()
