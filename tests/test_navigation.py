"""Regression coverage for terrain semantics, bounded search, routing, and objects."""

import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest

from autonomous_controller.nav_astar import NavAstar
from autonomous_controller.nav_core import NavCore
from autonomous_controller.pathfinder import astar
from autonomous_controller.walkable_map import RomPassability
from autonomous_controller.world_graph import WorldGraph
from memory_state.game_state import PokemonGameState

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
class TerrainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = [ROOT / "pokered/constants/map_constants.asm", ROOT / "world_graph.json"]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise unittest.SkipTest("Local navigation assets required: " + ", ".join(missing))
        cls.terrain = RomPassability(ROOT / "pokered")
        cls.graph = WorldGraph(ROOT / "world_graph.json")

    def test_room_dimensions_are_player_steps(self):
        self.assertEqual(
            (self.terrain.map_width("REDS_HOUSE_2F"), self.terrain.map_height("REDS_HOUSE_2F")),
            (8, 8),
        )
        self.assertEqual(self.terrain.map_height("ROUTE_1"), 36)

    def test_room_floor_and_furniture(self):
        self.assertTrue(self.terrain.is_passable("REDS_HOUSE_2F", 3, 6))
        self.assertFalse(self.terrain.is_passable("REDS_HOUSE_2F", 3, 5))
        self.assertTrue(self.terrain.is_passable("REDS_HOUSE_2F", 7, 1))

    def test_unknown_and_outside_are_blocked(self):
        for name, x, y in [
            ("MISSING", 0, 0),
            ("REDS_HOUSE_2F", -1, 3),
            ("REDS_HOUSE_2F", 8, 3),
            ("REDS_HOUSE_2F", 3, 8),
        ]:
            self.assertFalse(self.terrain.is_passable(name, x, y))

    def test_route_one_requires_real_detours(self):
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

    def test_connection_offsets_and_reverse_edges(self):
        north = set(self.terrain.connection_tiles("ROUTE_1", "VIRIDIAN_CITY", "up", -5))
        south = set(self.terrain.connection_tiles("VIRIDIAN_CITY", "ROUTE_1", "down", 5))
        self.assertIn(((11, 0), (21, 35)), north)
        self.assertEqual(north, {(b, a) for a, b in south})
        west = set(self.terrain.connection_tiles("VIRIDIAN_CITY", "ROUTE_22", "left", 4))
        east = set(self.terrain.connection_tiles("ROUTE_22", "VIRIDIAN_CITY", "right", -4))
        self.assertTrue(west)
        self.assertEqual(west, {(b, a) for a, b in east})

    def test_forest_uses_accessible_south_gate(self):
        route = self.graph.terrain_route("ROUTE_2", "VIRIDIAN_FOREST", (7, 71), self.terrain)
        self.assertEqual(route, ["ROUTE_2", "VIRIDIAN_FOREST_SOUTH_GATE", "VIRIDIAN_FOREST"])


class SearchTests(unittest.TestCase):
    def test_already_at_goal(self):
        self.assertEqual(astar(0, 0, 0, 0, lambda x, y: True), [])

    def test_blocked_goal_is_not_adjusted_to_a_neighbor(self):
        self.assertIsNone(astar(0, 0, 1, 0, lambda x, y: (x, y) != (1, 0)))

    def test_expansion_budget_is_a_real_limit(self):
        calls = []

        def passable(x, y):
            calls.append((x, y))
            return True

        self.assertIsNone(astar(0, 0, 1000, 1000, passable, max_steps=5))
        self.assertLessEqual(len(calls), 21)

    def test_directional_edges_and_forbidden_warp_detour(self):
        def allowed(x, y):
            return 0 <= x < 3 and 0 <= y < 2 and (x, y) != (1, 0)

        path = astar(0, 0, 2, 0, allowed)
        self.assertEqual(path, ["down", "right", "right", "up"])
        self.assertIsNone(
            astar(0, 0, 2, 0, allowed, can_step=lambda x, y, nx, ny: not (x == 0 and nx == 1))
        )


class ObjectTests(unittest.TestCase):
    def test_live_objects_distinguish_visibility_and_reserve_moving_square(self):
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

    def test_unreachable_navigation_stops_after_bounded_waits(self):
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


if __name__ == "__main__":
    unittest.main()
