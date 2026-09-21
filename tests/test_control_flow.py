"""Bounded dialogue/battle waits and travel resumption."""

from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

import pytest
from pyboy.utils import WindowEvent

from autonomous_controller import scripted_demo as main
from autonomous_controller.battle_controller import BattleController
from autonomous_controller.controller import AutonomousController
from autonomous_controller.emulator_session import EmulatorClosed
from autonomous_controller.interrupt_handler import (
    BattleInterrupt,
    ControlTimeout,
    InterruptHandler,
)


def test_dialogue_budget_and_timeout_propagation(subtests):
    for limit in [0, 1, 2, 7, 30]:
        with subtests.test(limit=limit):
            emulator = Mock()
            gs = SimpleNamespace(dialog="Persistent prompt", map={"in_battle": False})
            handler = InterruptHandler(emulator, gs)
            assert not handler.wait_for_control(timeout_frames=limit)
            assert emulator.tick.call_count == limit
            if limit > 1:
                emulator.send_input.assert_called_with(WindowEvent.RELEASE_BUTTON_A)

    with subtests.test(scenario="control_timeout_stops_navigation_with_reason"):
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

    with subtests.test(scenario="interrupted_dialogue_raises_timeout_and_releases_on_close"):
        emulator = Mock()
        state = SimpleNamespace(
            dialog="Prompt", map={"in_battle": False, "player_x": 0, "player_y": 0}
        )
        handler = InterruptHandler(emulator, state)
        handler.CONTROL_TIMEOUT = 2
        with pytest.raises(ControlTimeout):
            handler.check_and_handle()
        emulator.tick.side_effect = [True, EmulatorClosed()]
        with pytest.raises(EmulatorClosed):
            handler.wait_for_control(timeout_frames=10)
        emulator.send_input.assert_called_with(WindowEvent.RELEASE_BUTTON_A)


def test_battle_wait_budgets(subtests):
    for in_battle in [True, False]:
        with subtests.test(in_battle=in_battle):
            emulator = Mock()
            state = SimpleNamespace(dialog="Persistent text", mem=Mock())
            state.mem.read_byte.return_value = int(in_battle)
            battle = BattleController(emulator, state)
            wait = battle.wait_for_turn if in_battle else battle.clear_post_battle_text
            assert wait(timeout=2) is False
            assert emulator.tick.call_count == 2
            emulator.send_input.assert_called_with(WindowEvent.RELEASE_BUTTON_B)

    for menu, forced in (
        ("moves", False),
        ("bag", False),
        ("party", False),
        ("party", True),
        ("next_pokemon", True),
    ):
        with subtests.test(menu=menu, forced=forced):
            emulator = Mock()
            battle = BattleController(emulator, SimpleNamespace(mem=Mock()))
            battle.is_in_battle = lambda: True
            battle.menu_state = lambda: menu
            battle.needs_switch = lambda: forced
            assert battle.wait_for_turn(timeout=60) is forced
            emulator.send_input.assert_not_called()


def test_battle_loop_handles_replacement_and_rejected_actions(subtests):
    for replacement in (True, False):
        with subtests.test(replacement=replacement):
            battle = Mock()
            battle.is_in_battle.return_value = True
            battle.wait_for_turn.return_value = True
            battle.is_wild_battle.return_value = False
            battle.needs_switch.return_value = replacement
            battle.available_switches.return_value = [1]

            def switch(slot):
                battle.is_in_battle.return_value = False
                return True

            battle.switch.side_effect = switch
            battle.fight.return_value = False
            state = SimpleNamespace(mem=Mock(), party_pokemon=[])
            main._run_battle_loop(battle, state)
            if replacement:
                battle.switch.assert_called_once_with(1)
                battle.fight.assert_not_called()
            else:
                battle.fight.assert_called_once_with(move_index=0)
                battle.switch.assert_not_called()


def test_travel_resumes_after_battles_and_stops_when_stalled(subtests):
    for progress, unfinished in [(True, False), (False, False), (True, True)]:
        with subtests.test(progress=progress, unfinished=unfinished):
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
            expected = (
                1 if unfinished else encounters + 1 if progress else main.MAX_STALLED_INTERRUPTS
            )
            assert controller.go_to.call_count == expected
