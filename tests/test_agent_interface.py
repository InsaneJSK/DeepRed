"""Observation contract, action validation, and interrupted travel lifecycle."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from autonomous_controller.agent_interface import AgentInterface
from autonomous_controller.emulator_session import EmulatorClosed
from autonomous_controller.interrupt_handler import BattleInterrupt


def interface():
    state = SimpleNamespace(
        map={"map_id": 0, "in_battle": False, "player_x": 5, "player_y": 5},
        dialog="",
        party_pokemon=[],
        map_objects=[],
        items=[("POTION", 2)],
        money=3000,
        badges=[],
        mem=Mock(),
    )
    navigation = Mock(last_error="Story gate")
    state.mem.read_byte.return_value = 0
    navigation.graph.map_name.return_value = "PALLET_TOWN"
    navigation.graph.warps.return_value = []
    navigation.graph.connections.return_value = {"north": {"map": "ROUTE_1"}}
    navigation.graph.map_id.side_effect = {"ROUTE_1": 12, "VIRIDIAN_CITY": 1}.get
    battle = Mock(last_result=None)
    battle.menu_state.return_value = "ended"
    battle.is_in_battle.side_effect = lambda: state.map["in_battle"]
    battle.is_wild_battle.return_value = False
    battle.needs_switch.return_value = False
    battle.available_switches.return_value = [1]
    battle.active_mon_slot.return_value = 0
    return AgentInterface(None, state, navigation=navigation, battle=battle)


def test_observation_contract_and_decisions(subtests):
    agent = interface()
    for menu, forced, decision, actions in (
        ("ended", False, "overworld", ["navigate", "interact"]),
        ("main", False, "battle", ["fight", "switch", "use_item", "run"]),
        ("party", True, "switch", ["switch"]),
        ("moves", False, "menu", ["cancel"]),
        ("yes_no", False, "menu", []),
        ("text", False, "advance", ["advance"]),
    ):
        with subtests.test(menu=menu):
            agent.gs.map["in_battle"] = menu != "ended"
            agent.battle.menu_state.return_value = menu
            agent.battle.needs_switch.return_value = forced
            observation = agent.observe()
            assert observation["decision"] == decision
            assert observation["actions"] == actions
            assert observation["exits"][0]["access"] == "unverified"
            assert json.loads(json.dumps(observation)) == observation
    agent.gs.party_pokemon = [
        {
            "species_name": "CHARMANDER",
            "nickname": "CHARMANDER",
            "level": 5,
            "current_hp": "3/20",
            "status": "OK",
            "type1": "FIRE",
            "type2": None,
            "moves, pp": [("SCRATCH", 30)],
        }
    ]
    assert agent.observe()["party"][0]["hp"] == 3
    assert agent.observe()["party"][0]["moves"][0]["index"] == 0
    assert len(json.loads(json.dumps(agent.action_schema()))["oneOf"]) == 11
    agent.navigation.go_to.assert_not_called()
    agent.battle.wait_for_turn.assert_not_called()


def test_action_validation_and_dispatch(subtests):
    for request in (
        None,
        {},
        {"action": "unknown"},
        {"action": "navigate"},
        {"action": "navigate", "destination": "VICTORY_ROAD"},
        {"action": "run", "extra": 1},
        {"action": "fight", "move_index": True},
        {"action": "fight", "move_index": -1},
        {"action": "run"},
    ):
        with subtests.test(request=request):
            agent = interface()
            assert agent.execute(request)["status"] == "rejected"
            agent.navigation.go_to.assert_not_called()
            agent.battle.run.assert_not_called()
            agent.battle.fight.assert_not_called()
    for action, arguments in (
        ("fight", {"move_index": 1}),
        ("run", {}),
        ("switch", {"party_index": 1}),
        ("use_item", {"bag_index": 0, "party_index": 1}),
    ):
        with subtests.test(action=action):
            agent = interface()
            agent.gs.map["in_battle"] = True
            agent.battle.menu_state.return_value = "main"
            method = getattr(agent.battle, action)
            method.return_value = True
            result = agent.execute({"action": action, **arguments})
            assert result["status"] == ("submitted" if action in ("fight", "run") else "completed")
            method.assert_called_once_with(**arguments)
    with subtests.test(action="interact"):
        agent = interface()
        agent.navigation.interact.return_value = True
        result = agent.execute({"action": "interact", "object_slot": 5})
        assert result["status"] == "completed"
        agent.navigation.interact.assert_called_once_with(object_slot=5)
    with subtests.test(action="distant destination"):
        agent = interface()
        agent.navigation.go_to.return_value = True
        assert (
            agent.execute({"action": "navigate", "destination": "viridian_city"})["status"]
            == "completed"
        )
        agent.navigation.go_to.assert_called_once_with("VIRIDIAN_CITY")


def test_interrupted_travel_resume_and_failure(subtests):
    agent = interface()

    def interrupted(destination):
        agent.gs.map["in_battle"] = True
        agent.battle.menu_state.return_value = "main"
        raise BattleInterrupt()

    agent.navigation.go_to.side_effect = interrupted
    result = agent.execute({"action": "navigate", "destination": "route_1"})
    assert result["status"] == "interrupted"
    assert result["observation"]["pending_destination"] == "ROUTE_1"
    assert agent.execute({"action": "resume"})["status"] == "rejected"
    agent.gs.map["in_battle"] = False
    agent.battle.menu_state.return_value = "ended"
    agent.navigation.go_to.side_effect = None
    agent.navigation.go_to.return_value = True
    assert agent.execute({"action": "resume"})["status"] == "completed"
    assert agent.pending_destination is None
    for outcome, status in ((False, "blocked"), (RuntimeError("cursor"), "failed")):
        with subtests.test(status=status):
            agent.navigation.go_to.side_effect = outcome if isinstance(outcome, Exception) else None
            agent.navigation.go_to.return_value = outcome
            assert (
                agent.execute({"action": "navigate", "destination": "ROUTE_1"})["status"] == status
            )
    agent.navigation.go_to.side_effect = EmulatorClosed()
    with pytest.raises(EmulatorClosed):
        agent.execute({"action": "navigate", "destination": "ROUTE_1"})
