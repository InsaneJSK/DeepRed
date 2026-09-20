"""Regressions for failures confirmed during the code review."""

from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

import pytest
from pyboy.utils import WindowEvent

import main
from autonomous_controller.battle_controller import BattleController
from autonomous_controller.build_world_graph import resolve_last_map
from autonomous_controller.controller import AutonomousController
from autonomous_controller.emulator_session import EmulatorClosed, open_emulator
from autonomous_controller.game_data import load_bundle
from autonomous_controller.hop_executor import HopExecutor
from autonomous_controller.interrupt_handler import (
    BattleInterrupt,
    ControlTimeout,
    InterruptHandler,
)
from autonomous_controller.save_state import create_checkpoint
from memory_state.misc_info import MiscInfo
from scratch.navigation_regression import verify_expected_block


@pytest.mark.parametrize("limit", [0, 1, 2, 7, 30])
def test_persistent_dialogue_obeys_total_frame_budget(limit):
    emulator = Mock()
    gs = SimpleNamespace(dialog="Persistent prompt", map={"in_battle": False})
    handler = InterruptHandler(emulator, gs)
    assert not handler.wait_for_control(timeout_frames=limit)
    assert emulator.tick.call_count == limit
    if limit > 1:
        emulator.send_input.assert_called_with(WindowEvent.RELEASE_BUTTON_A)


def test_control_timeout_stops_navigation_with_reason():
    controller = AutonomousController.__new__(AutonomousController)
    controller.gs = SimpleNamespace(map={"in_battle": False}, mem=Mock())
    controller.graph = Mock()
    controller.graph.terrain_route.return_value = ["A", "B"]
    controller.rom_pass = Mock()
    controller._map_name = lambda: "A"
    controller._pos = lambda: (0, 0)
    controller._execute_hop = Mock(side_effect=ControlTimeout("control budget exhausted"))
    assert controller.go_to("B") is False
    assert controller.last_error == "control budget exhausted"


def test_interrupted_dialogue_raises_timeout_and_releases_on_close():
    emulator = Mock()
    state = SimpleNamespace(dialog="Prompt", map={"in_battle": False, "player_x": 0, "player_y": 0})
    handler = InterruptHandler(emulator, state)
    handler.CONTROL_TIMEOUT = 2
    with pytest.raises(ControlTimeout):
        handler.check_and_handle()
    emulator.tick.side_effect = [True, EmulatorClosed()]
    with pytest.raises(EmulatorClosed):
        handler.wait_for_control(timeout_frames=10)
    emulator.send_input.assert_called_with(WindowEvent.RELEASE_BUTTON_A)


@pytest.mark.parametrize("in_battle", [True, False])
def test_battle_waits_count_button_frames(in_battle):
    emulator = Mock()
    state = SimpleNamespace(dialog="Persistent text", mem=Mock())
    state.mem.read_byte.return_value = int(in_battle)
    battle = BattleController(emulator, state)
    wait = battle.wait_for_turn if in_battle else battle.clear_post_battle_text
    assert wait(timeout=2) is False
    assert emulator.tick.call_count == 2
    emulator.send_input.assert_called_with(WindowEvent.RELEASE_BUTTON_B)


@pytest.mark.parametrize("progress, unfinished", [(True, False), (False, False), (True, True)])
def test_battles_resume_travel_but_stop_on_real_failure(progress, unfinished):
    emulator = Mock()
    emulator.tick.side_effect = [True, False]  # load tick, then final idle loop
    gs = SimpleNamespace(map={"map_id": 0, "player_x": 0, "player_y": 0})
    controller, battle = Mock(), Mock()
    battle.is_in_battle.return_value = False
    encounters = 12

    def navigate(_goal):
        if controller.go_to.call_count <= encounters:
            if progress:
                gs.map["player_y"] += 1
            raise BattleInterrupt()
        return True

    def combat(*args):
        battle.is_in_battle.return_value = unfinished

    controller.go_to.side_effect = navigate
    with (
        patch("builtins.open", mock_open(read_data=b"")),
        patch.object(main, "PokemonGameState", return_value=gs),
        patch.object(main, "AutonomousController", return_value=controller),
        patch.object(main, "BattleController", return_value=battle),
        patch.object(main, "_run_battle_loop", side_effect=combat),
    ):
        main._run_agent(emulator)
    expected = 1 if unfinished else encounters + 1 if progress else main.MAX_STALLED_INTERRUPTS
    assert controller.go_to.call_count == expected


def test_names_are_bounded_and_terminated_and_coins_are_decimal():
    memory = bytearray(65536)
    memory[0xD158:0xD163] = bytes([0x80, 0x50] + [0x99] * 9)
    memory[0xD34A:0xD355] = bytes(range(0x80, 0x8A)) + bytes([0x50])
    info = MiscInfo(SimpleNamespace(memory=memory))
    assert info.names == ("A", "ABCDEFGHIJ")
    for raw, expected in [(b"\x00\x00", 0), (b"\x12\x34", 1234), (b"\x99\x99", 9999)]:
        memory[0xD5A4:0xD5A6] = raw
        assert info.read_coins == expected


def test_ambiguous_return_is_preserved_without_guessing():
    def warp(destination):
        return {"dest_map": destination, "dest_warp_index": 1, "x": 0, "y": 0}

    graph = {"A": [warp("ROOM")], "B": [warp("ROOM")], "ROOM": [warp("LAST_MAP")]}
    result = resolve_last_map(graph, {"A", "B"})["ROOM"][0]
    assert result["dest_map"] == "LAST_MAP"
    assert result["dest_map_candidates"] == ["A", "B"]


@pytest.mark.parametrize("door_destination", [2, 3])
def test_dynamic_return_checks_destination_after_approaching_door(door_destination):
    hop = HopExecutor()
    current_map = [1]
    memory = bytearray(65536)
    memory[0xD365] = 3  # A gate script can change this during the approach.
    hop.gs = SimpleNamespace(mem=SimpleNamespace(read_byte=lambda address: memory[address]))
    hop.graph = Mock()
    hop.graph.map_id.return_value = 2
    hop.graph.map_name.side_effect = {2: "TARGET", 3: "OTHER"}.get
    hop.rom_pass = Mock()
    hop.rom_pass.is_passable.side_effect = lambda name, x, y: (x, y) == (2, 1)
    hop._map_id = lambda: current_map[0]
    hop._map_name = lambda: "GATE"
    hop._warp_tiles = lambda: {(2, 2)}
    hop._plan_path = lambda *args: ["down"]
    hop.interrupt = SimpleNamespace(was_displaced=False)
    hop.last_error = ""

    def approach(*args, **kwargs):
        memory[0xD365] = door_destination
        return True

    hop.navigate_to_tile = approach
    hop._step = Mock(side_effect=lambda direction: current_map.__setitem__(0, 2))
    warp = {"x": 2, "y": 2, "dest_map": "LAST_MAP"}
    assert hop._execute_warp_hop(warp, "TARGET") is (door_destination == 2)
    if door_destination == 2:
        hop._step.assert_called_once_with("down")
    else:
        hop._step.assert_not_called()
        assert hop.last_error == "Return door leads to OTHER, not TARGET"


def test_real_cave_returns_and_resolved_warp_indices():
    graph = load_bundle()["graph"]
    for entrance, outside in [
        ("DIGLETTS_CAVE_ROUTE_2", "ROUTE_2"),
        ("DIGLETTS_CAVE_ROUTE_11", "ROUTE_11"),
    ]:
        for warp in graph["maps"][entrance]["warps"][:2]:
            assert warp["dest_map"] == outside
            assert 1 <= warp["dest_warp_index"] <= len(graph["maps"][outside]["warps"])


def test_checkpoint_refuses_existing_output_before_starting_emulator(tmp_path):
    source = tmp_path / "original.state"
    source.write_bytes(b"original")
    other = tmp_path / "other.state"
    other.write_bytes(b"other")
    with patch("autonomous_controller.save_state.open_emulator") as launch:
        for destination in (source, other):
            with pytest.raises(ValueError, match="overwritten"):
                create_checkpoint("unused.gb", source, destination)
        launch.assert_not_called()
    assert source.read_bytes() == b"original"
    assert other.read_bytes() == b"other"


def test_utility_close_and_error_always_stop_without_saving():
    for error in (EmulatorClosed(), RuntimeError("failure")):
        emulator = Mock()
        with patch("autonomous_controller.emulator_session.PyBoy", return_value=emulator):
            if isinstance(error, EmulatorClosed):
                with open_emulator("unused.gb"):
                    raise error
            else:
                with pytest.raises(RuntimeError):
                    with open_emulator("unused.gb"):
                        raise error
        emulator.stop.assert_called_once_with(save=False)


def test_expected_gate_rejects_unrelated_failure():
    controller = SimpleNamespace(
        _map_name=lambda: "VIRIDIAN_CITY", _pos=lambda: (19, 10), last_error="script"
    )
    verify_expected_block(controller, "VIRIDIAN_CITY", (19, 10), "script")
    for map_name, position, reason in [
        ("ROUTE_1", (19, 10), "script"),
        ("VIRIDIAN_CITY", (0, 0), "script"),
        ("VIRIDIAN_CITY", (19, 10), "no route"),
    ]:
        with pytest.raises(AssertionError, match="Wrong blockage"):
            verify_expected_block(controller, map_name, position, reason)
