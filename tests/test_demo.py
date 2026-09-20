"""Human commands preserve the shared action contract and don't act on inspection."""

from contextlib import nullcontext
from threading import Event
from unittest.mock import Mock

import pytest
from pyboy.utils import WindowEvent

from autonomous_controller.emulator_session import EmulatorClosed
from demo import (
    ROOT,
    advance_to_choice,
    main,
    parse_command,
    run_cli,
    save_checkpoint,
    window_input,
)


def test_cli_commands(subtests):
    for text, expected in (
        ("navigate ROUTE_1", {"action": "navigate", "destination": "ROUTE_1"}),
        ("fight 0", {"action": "fight", "move_index": 0}),
        (
            "use_item 3 1 0",
            {"action": "use_item", "bag_index": 3, "party_index": 1, "move_index": 0},
        ),
        ("resume", {"action": "resume"}),
        ("interact 5", {"action": "interact", "object_slot": 5}),
        ("", None),
    ):
        with subtests.test(command=text):
            assert parse_command(text) == expected
    for text in ("fight", "fight 0 1", "fight -1", "fight zero", "run extra", "unknown"):
        with subtests.test(invalid=text):
            with pytest.raises(ValueError):
                parse_command(text)


def test_cli_dispatch_and_exit(monkeypatch, subtests):
    monkeypatch.setattr("demo.show", Mock())
    for ending in ("quit", EOFError(), KeyboardInterrupt()):
        with subtests.test(ending=str(ending)):
            agent = Mock()
            agent.observe.return_value = {}
            agent.execute.return_value = {
                "status": "rejected",
                "detail": "Not ready",
                "observation": {},
            }
            commands = Mock(side_effect=["help", "observe", "json", "wrong", "fight 0", ending])
            run_cli(agent, read=commands)
            agent.execute.assert_called_once_with({"action": "fight", "move_index": 0})
    save = Mock(side_effect=[ROOT / "saves/test.state", FileExistsError(), OSError("disk full")])
    commands = Mock(side_effect=["/save", '/save "saves/my checkpoint.state"', "/save x", "quit"])
    agent.reset_mock()
    run_cli(agent, read=commands, save=save)
    assert [call.args for call in save.call_args_list] == [
        (None,),
        ("saves/my checkpoint.state",),
        ("x",),
    ]
    agent.execute.assert_not_called()


def test_auto_advance_stops_at_choices_errors_and_limit(subtests):
    for actions in (["fight", "run"], ["advance", "cancel"], [], ["switch"]):
        with subtests.test(actions=actions):
            agent = Mock()
            observation = {"actions": actions}
            assert advance_to_choice(agent, observation) == observation
            agent.execute.assert_not_called()
    for status in ("completed", "timeout", "failed", "rejected", "interrupted"):
        with subtests.test(status=status):
            agent = Mock()
            next_state = {"actions": ["advance"] if status != "completed" else ["fight", "run"]}
            agent.execute.return_value = {
                "status": status,
                "detail": None,
                "observation": next_state,
            }
            assert advance_to_choice(agent, {"actions": ["advance"]}) == next_state
            agent.execute.assert_called_once_with({"action": "advance"})
    agent = Mock()
    agent.execute.return_value = {
        "status": "completed",
        "detail": None,
        "observation": {"actions": ["advance"]},
    }
    advance_to_choice(agent, {"actions": ["advance"]})
    assert agent.execute.call_count == 8


def test_demo_modes_and_paused_window_close(monkeypatch, tmp_path, subtests):
    save = tmp_path / "test.state"
    save.write_bytes(b"test")
    monkeypatch.setattr("demo.validate_rom", Mock())
    monkeypatch.setattr("demo.PokemonGameState", Mock())
    monkeypatch.setattr("demo.AgentInterface", Mock())
    run = Mock()
    monkeypatch.setattr("demo.run_cli", run)
    for flags, window, speed, automatic in (
        ([], "null", 0, False),
        (["--no-headless", "--auto-advance"], "SDL2", 1, True),
        (["--headless", "--no-auto-advance"], "null", 0, False),
    ):
        with subtests.test(flags=flags):
            session = Mock()
            opener = Mock(return_value=nullcontext(session))
            monkeypatch.setattr("demo.open_emulator", opener)
            main(["--save", str(save), *flags])
            assert opener.call_args.kwargs["window"] == window
            session.set_emulation_speed.assert_called_once_with(speed)
            assert run.call_args.kwargs["auto_advance"] is automatic
    released = Event()
    monkeypatch.setattr("builtins.input", lambda prompt: released.wait(2))
    session = Mock()
    session.tick.side_effect = [True, EmulatorClosed()]
    try:
        with pytest.raises(EmulatorClosed):
            window_input(session, "")
    finally:
        released.set()
    assert [call.args[0] for call in session.send_input.call_args_list] == [
        WindowEvent.PAUSE,
        WindowEvent.UNPAUSE,
    ]


@pytest.mark.integration
def test_checkpoint_save_and_demo_reload(tmp_path, monkeypatch):
    from autonomous_controller.emulator_session import open_emulator
    from memory_state.game_state import PokemonGameState

    rom, initial = ROOT / "Pokemon_Red/Red.gb", ROOT / "saves/in-room-start.state"
    if not rom.exists() or not initial.exists():
        pytest.skip("Local ROM and bedroom checkpoint required")
    monkeypatch.setattr("demo.ROOT", tmp_path)
    with open_emulator(rom, window="null", sound_emulated=False) as session:
        session.set_emulation_speed(0)
        with initial.open("rb") as stream:
            session.load_state(stream)
        session.tick()
        expected = PokemonGameState(session).map
        # A visible CLI saves after input while UNPAUSE is still queued.
        session.send_input(WindowEvent.PAUSE)
        session.tick()
        session.send_input(WindowEvent.UNPAUSE)
        checkpoint = save_checkpoint(session)
        assert checkpoint.parent == tmp_path / "saves"
        original = checkpoint.read_bytes()
        assert original
        with pytest.raises(FileExistsError):
            save_checkpoint(session, checkpoint)
        assert checkpoint.read_bytes() == original
        named = save_checkpoint(session, tmp_path / "named checkpoint.state")
        assert named.read_bytes() == original

    def inspect(agent, **kwargs):
        assert agent.gs.map == expected
        assert not agent.navigation.pyboy.paused
        assert callable(kwargs["save"])

    inspect_mock = Mock(side_effect=inspect)
    monkeypatch.setattr("demo.run_cli", inspect_mock)
    main(["--rom", str(rom), "--save", str(named)])
    inspect_mock.assert_called_once()
