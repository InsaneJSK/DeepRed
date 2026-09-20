"""Terrain, routing, local movement and route failure contracts."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pyboy import PyBoy
from pyboy.utils import WindowEvent

from autonomous_controller.agent_interface import AgentInterface
from autonomous_controller.build_world_graph import resolve_last_map
from autonomous_controller.controller import AutonomousController
from autonomous_controller.emulator_session import EmulatorSession
from autonomous_controller.game_data import load_bundle
from autonomous_controller.hop_executor import HopExecutor
from autonomous_controller.interrupt_handler import BattleInterrupt
from autonomous_controller.nav_astar import NavAstar
from autonomous_controller.nav_core import NavCore
from autonomous_controller.pathfinder import astar
from autonomous_controller.walkable_map import RomPassability
from autonomous_controller.world_graph import WorldGraph
from main import _run_battle_loop
from memory_state.game_state import PokemonGameState
from scratch.navigation_regression import verify_expected_block


class TerrainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.terrain = RomPassability()
        cls.graph = WorldGraph()

    def test_terrain_dimensions_and_collision(self):
        with self.subTest(scenario="room_dimensions_are_player_steps"):
            self.assertEqual(
                (self.terrain.map_width("REDS_HOUSE_2F"), self.terrain.map_height("REDS_HOUSE_2F")),
                (8, 8),
            )
            self.assertEqual(self.terrain.map_height("ROUTE_1"), 36)
        with self.subTest(scenario="room_floor_and_furniture"):
            self.assertTrue(self.terrain.is_passable("REDS_HOUSE_2F", 3, 6))
            self.assertFalse(self.terrain.is_passable("REDS_HOUSE_2F", 3, 5))
            self.assertTrue(self.terrain.is_passable("REDS_HOUSE_2F", 7, 1))
        with self.subTest(scenario="unknown_and_outside_are_blocked"):
            for name, x, y in [
                ("MISSING", 0, 0),
                ("REDS_HOUSE_2F", -1, 3),
                ("REDS_HOUSE_2F", 8, 3),
                ("REDS_HOUSE_2F", 3, 8),
            ]:
                self.assertFalse(self.terrain.is_passable(name, x, y))

    def test_routes_and_connections(self):
        with self.subTest(scenario="route_one_requires_real_detours"):
            terrain = self.terrain
            path = astar(
                10,
                35,
                11,
                0,
                lambda x, y: terrain.is_passable("ROUTE_1", x, y),
                can_step=lambda x, y, nx, ny: terrain.can_step("ROUTE_1", x, y, nx, ny),
            )
            self.assertIsNotNone(path)
            self.assertGreater(len(path), 36)
            self.assertLess(len(path), 65)
        with self.subTest(scenario="connection_offsets_and_reverse_edges"):
            north = set(self.terrain.connection_tiles("ROUTE_1", "VIRIDIAN_CITY", "up", -5))
            south = set(self.terrain.connection_tiles("VIRIDIAN_CITY", "ROUTE_1", "down", 5))
            self.assertIn(((11, 0), (21, 35)), north)
            self.assertEqual(north, {(b, a) for a, b in south})
            west = set(self.terrain.connection_tiles("VIRIDIAN_CITY", "ROUTE_22", "left", 4))
            east = set(self.terrain.connection_tiles("ROUTE_22", "VIRIDIAN_CITY", "right", -4))
            self.assertTrue(west)
            self.assertEqual(west, {(b, a) for a, b in east})


class SearchTests(unittest.TestCase):
    def test_bounded_search_and_obstacles(self):
        with self.subTest(scenario="already_at_goal"):
            self.assertEqual(astar(0, 0, 0, 0, lambda x, y: True), [])
        with self.subTest(scenario="blocked_goal_is_not_adjusted_to_a_neighbor"):
            self.assertIsNone(astar(0, 0, 1, 0, lambda x, y: (x, y) != (1, 0)))
        with self.subTest(scenario="expansion_budget_is_a_real_limit"):
            calls = []

            def passable(x, y):
                calls.append((x, y))
                return True

            self.assertIsNone(astar(0, 0, 1000, 1000, passable, max_steps=5))
            self.assertLessEqual(len(calls), 21)
        with self.subTest(scenario="directional_edges_and_forbidden_warp_detour"):

            def allowed(x, y):
                return 0 <= x < 3 and 0 <= y < 2 and (x, y) != (1, 0)

            path = astar(0, 0, 2, 0, allowed)
            self.assertEqual(path, ["down", "right", "right", "up"])
            self.assertIsNone(
                astar(0, 0, 2, 0, allowed, can_step=lambda x, y, nx, ny: not (x == 0 and nx == 1))
            )


class ObjectTests(unittest.TestCase):
    def test_local_navigation_objects_and_unreachable_goal(self):
        with self.subTest(scenario="live_objects_distinguish_visibility_and_reserve_moving_square"):
            memory = bytearray(65536)
            memory[0xD35E] = 0
            memory[0xD362], memory[0xD361] = 2, 2
            memory[0xC110], memory[0xC111], memory[0xC112] = 3, 3, 10
            memory[0xC115] = 1
            memory[0xC214], memory[0xC215] = 9, 10  # destination (6,5)
            memory[0xC120], memory[0xC122] = 4, 255
            memory[0xC224], memory[0xC225] = 8, 8
            gs = PokemonGameState(SimpleNamespace(memory=memory))
            core = NavCore()
            core.gs = gs
            self.assertEqual(core._occupied_tiles(), {(5, 5), (6, 5)})
            self.assertEqual(len(gs.map_objects), 2)
        with self.subTest(scenario="unreachable_navigation_stops_after_bounded_waits"):

            class Blocked(NavAstar):
                def _map_id(self):
                    return 1

                def _pos(self):
                    return (0, 0)

                def _plan_path(self, *args):
                    return None

            nav = Blocked()
            ticks = []
            nav.pyboy = SimpleNamespace(tick=lambda n: ticks.append(n))
            nav.interrupt = SimpleNamespace(check_and_handle=lambda: None)
            self.assertFalse(nav.navigate_to_tile(1, 1))
            self.assertEqual(ticks, [30, 30])
            self.assertEqual(nav.last_nav_reason, "no_path")


def test_return_doors(subtests):
    with subtests.test(scenario="ambiguous_return_is_preserved_without_guessing"):

        def warp(destination):
            return {"dest_map": destination, "dest_warp_index": 1, "x": 0, "y": 0}

        graph = {"A": [warp("ROOM")], "B": [warp("ROOM")], "ROOM": [warp("LAST_MAP")]}
        result = resolve_last_map(graph, {"A", "B"})["ROOM"][0]
        assert result["dest_map"] == "LAST_MAP"
        assert result["dest_map_candidates"] == ["A", "B"]

    for door_destination in [2, 3]:
        with subtests.test(door_destination=door_destination):
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

    with subtests.test(scenario="real_cave_returns_and_resolved_warp_indices"):
        graph = load_bundle()["graph"]
        for entrance, outside in [
            ("DIGLETTS_CAVE_ROUTE_2", "ROUTE_2"),
            ("DIGLETTS_CAVE_ROUTE_11", "ROUTE_11"),
        ]:
            for warp in graph["maps"][entrance]["warps"][:2]:
                assert warp["dest_map"] == outside
                assert 1 <= warp["dest_warp_index"] <= len(graph["maps"][outside]["warps"])


def test_story_gate_requires_exact_failure(subtests):
    with subtests.test(scenario="expected_gate_rejects_unrelated_failure"):
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


def test_npc_interaction_rechecks_target_before_confirming(subtests):
    for scenario in ("dialogue", "hidden", "moving", "no_path", "missing"):
        with subtests.test(scenario=scenario):
            nav = AutonomousController.__new__(AutonomousController)
            obj = {
                "slot": 5,
                "picture_id": 3,
                "x": 5,
                "y": 2,
                "visible": scenario != "hidden",
                "moving": scenario == "moving",
            }
            nav.gs = SimpleNamespace(
                map={"in_battle": False, "player_facing": "UP"},
                dialog="",
                map_objects=[] if scenario == "missing" else [obj],
            )
            nav.gs.mem = SimpleNamespace(read_byte=lambda address: 255)
            nav.rom_pass = Mock()
            nav._map_name = lambda: "OAKS_LAB"
            nav.pyboy = Mock()
            nav.interrupt = Mock()
            nav._map_id = lambda: 40
            nav._warp_tiles = lambda: {(5, 11)}
            nav._plan_path = lambda position, forbidden: (
                [] if position == (5, 3) and scenario != "no_path" else None
            )
            nav._pos = lambda: (5, 3)
            nav.navigate_to_tile = Mock(return_value=True)

            def press(button):
                if button == WindowEvent.PRESS_BUTTON_A:
                    nav.gs.dialog = "OAK: Oh! A parcel for me?"

            nav.press = Mock(side_effect=press)
            if scenario == "missing":
                with pytest.raises(ValueError):
                    nav.interact(5)
            else:
                assert nav.interact(5) is (scenario == "dialogue")
            confirmations = [
                c for c in nav.press.call_args_list if c.args[0] == WindowEvent.PRESS_BUTTON_A
            ]
            assert len(confirmations) == (1 if scenario == "dialogue" else 0)
            assert nav.navigate_to_tile.call_count <= 3
            nav.interrupt.wait_for_control.assert_not_called()


@pytest.mark.integration
def test_oak_parcel_delivery_through_interaction():
    root = Path(__file__).resolve().parents[1]
    rom, save = root / "Pokemon_Red/Red.gb", root / "saves/oak-room-battle.state"
    if not rom.exists() or not save.exists():
        pytest.skip("Local ROM and Oak-room save required")
    from autonomous_controller.game_data import validate_rom

    validate_rom(rom)
    game = PyBoy(str(rom), window="null", sound_emulated=False)
    try:
        game.set_emulation_speed(0)
        with save.open("rb") as stream:
            game.load_state(stream)
        session = EmulatorSession(game)
        session.tick()
        state = PokemonGameState(session)
        agent = AgentInterface(session, state)
        for destination in ("VIRIDIAN_MART", "OAKS_LAB"):
            for _ in range(20):
                if agent.battle.is_in_battle():
                    _run_battle_loop(agent.battle, state)
                    assert not agent.battle.is_in_battle()
                try:
                    if agent.navigation.go_to(destination):
                        break
                except BattleInterrupt:
                    continue
                # The Mart clerk approaches us and hands over the parcel.
                assert agent.navigation.last_error == "A game script moved or stopped the player."
            else:
                pytest.fail(f"Could not reach {destination}")
        assert any("PARCEL" in name for name, _ in state.items)
        result = agent.execute({"action": "interact", "object_slot": 5})
        assert result["status"] == "completed", result["detail"]
        assert "OAK" in result["observation"]["dialogue"].upper()
        assert result["observation"]["decision"] == "advance"
        result = agent.execute({"action": "advance"})
        assert result["status"] == "completed", result["detail"]
        assert not any("PARCEL" in name for name, _ in state.items)

        def travel(destination):
            for _ in range(30):
                if agent.battle.is_in_battle():
                    _run_battle_loop(agent.battle, state)
                try:
                    if agent.navigation.go_to(destination):
                        return
                except BattleInterrupt:
                    continue
            pytest.fail(f"Could not reach {destination}")

        def settle():
            for _ in range(8):
                if agent.observe()["actions"] != ["advance"]:
                    return
                result = agent.execute({"action": "advance"})
                assert result["status"] == "completed", result
            pytest.fail("Service did not reach a decision")

        def action(name, **arguments):
            settle()
            result = agent.execute({"action": name, **arguments})
            assert result["status"] == "completed", result
            settle()

        travel("VIRIDIAN_MART")
        money = state.money
        action("interact", object_slot=1)
        assert agent.observe()["objects"][0]["name"] == "Clerk"
        action("choose", option_index=0)  # Buy
        action("choose", option_index=0)  # Poke Ball
        action("quantity", amount=3)
        assert agent.observe()["menu"]["kind"] == "yes_no"
        assert state.money == money  # No automatic purchase at the prompt.
        action("choose", option_index=0)
        assert state.money == money - 600
        assert ("POKé BALL", 3) in state.items
        action("cancel")
        action("choose", option_index=1)  # Sell
        action("choose", option_index=0)
        action("quantity", amount=1)
        action("choose", option_index=0)
        assert state.money == money - 500
        assert ("POKé BALL", 2) in state.items
        action("cancel")
        action("choose", option_index=2)  # Quit

        travel("VIRIDIAN_POKECENTER")
        # Damage only this disposable emulator instance, never the user's save.
        session.memory[0xD16C] = 0
        session.memory[0xD16D] = 1
        session.memory[0xD188] = 0
        action("interact", object_slot=1)
        assert agent.observe()["menu"]["kind"] == "heal"
        assert agent.observe()["party"][0]["hp"] == 1
        action("choose", option_index=0)
        member = agent.observe()["party"][0]
        assert member["hp"] == member["max_hp"]
        assert member["moves"][0]["pp"] > 0

        travel("VIRIDIAN_CITY")
        action("interact", object_slot=7)
        assert "hurry?" in state.dialog.lower()
        from unittest.mock import patch

        read_byte = state.mem.read_byte
        tutorial_seen = []

        def record_tutorial(address):
            value = read_byte(address)
            if address == 0xD05A and value == 1:
                tutorial_seen.append(True)
            return value

        with patch.object(state.mem, "read_byte", side_effect=record_tutorial):
            action("choose", option_index=1)  # No: show the catching demonstration.
        assert tutorial_seen
        assert agent.observe()["decision"] == "overworld"
    finally:
        game.stop(save=False)
