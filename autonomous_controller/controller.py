"""
autonomous_controller/controller.py

AutonomousController — assembles mixins into the top-level agent.

Architecture
------------
AutonomousController inherits from three mixins (Python MRO):

    NavCore       — raw movement primitives (_step, _dodge, press, etc.)
    NavAstar      — A*-based navigate_to_tile with oscillation/escape
    HopExecutor   — map-to-map hop execution (warps + connections)

Interrupt handling
------------------
InterruptHandler is wired into _step() (in NavCore) and raises
BattleInterrupt when a battle starts during navigation.  go_to() catches
BattleInterrupt and returns False so the higher-level AI agent can switch
to battle mode.

Future AI agent integration
---------------------------
When the battle-AI agent is implemented it can call:

    controller.go_to("VIRIDIAN_CITY")       # navigation
    controller.battle_action(BattleAction)  # battle moves (future)
    controller.use_item("POKEFLUTE")        # menu actions (future)

Usage
-----
    from autonomous_controller.controller import AutonomousController
    from memory_state.game_state import PokemonGameState

    gs         = PokemonGameState(pyboy)
    controller = AutonomousController(pyboy, gs, "world_graph.json")
    controller.go_to("VIRIDIAN_CITY")
"""

from pyboy.utils import WindowEvent  # pylint: disable=no-name-in-module

from autonomous_controller.constants       import DIRECTIONS
from autonomous_controller.world_graph     import WorldGraph
from autonomous_controller.walkable_map    import RomPassability
from autonomous_controller.interrupt_handler import InterruptHandler, BattleInterrupt
from autonomous_controller.nav_core        import NavCore
from autonomous_controller.nav_astar       import NavAstar
from autonomous_controller.hop_executor    import HopExecutor


class AutonomousController(NavCore, NavAstar, HopExecutor):
    """
    High-level autonomous navigation controller for Pokemon Red.

    All game state is read through PokemonGameState — no duplicate RAM reads.
    BFS routing is done over world_graph.json; tile-level pathfinding uses A*.
    """

    def __init__(
        self,
        pyboy,
        game_state,
        graph_path: str,
        pokered_root: str = "pokered",
    ):
        """
        Parameters
        ----------
        pyboy         : PyBoy instance
        game_state    : PokemonGameState instance
        graph_path    : path to world_graph.json
        pokered_root  : path to cloned pret/pokered repo (for ROM passability)
        """
        self.pyboy    = pyboy
        self.gs       = game_state       # PokemonGameState
        self.mem      = game_state.mem   # MemoryReader (reuse, no duplicate)
        self.graph    = WorldGraph(graph_path)
        self.game     = pyboy.game_wrapper
        self.rom_pass = RomPassability(pokered_root)
        self.interrupt = InterruptHandler(pyboy, game_state)

        self._expected_map_id: int = 0

        # Button press → release mapping (used by press() and _step())
        self._release_map = {
            WindowEvent.PRESS_ARROW_UP:     WindowEvent.RELEASE_ARROW_UP,
            WindowEvent.PRESS_ARROW_DOWN:   WindowEvent.RELEASE_ARROW_DOWN,
            WindowEvent.PRESS_ARROW_LEFT:   WindowEvent.RELEASE_ARROW_LEFT,
            WindowEvent.PRESS_ARROW_RIGHT:  WindowEvent.RELEASE_ARROW_RIGHT,
            WindowEvent.PRESS_BUTTON_A:     WindowEvent.RELEASE_BUTTON_A,
            WindowEvent.PRESS_BUTTON_B:     WindowEvent.RELEASE_BUTTON_B,
            WindowEvent.PRESS_BUTTON_START: WindowEvent.RELEASE_BUTTON_START,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def go_to(self, destination: str) -> bool:
        """
        Navigate from the current map to ``destination``.

        Plans a BFS route through the world graph and executes each map-to-map
        hop in sequence.

        Returns True if the player reaches the destination, False otherwise.
        If a battle starts during navigation, BattleInterrupt is caught here
        and the method returns False so the caller can hand off to the battle AI.
        """
        destination = destination.upper()

        try:
            current = self._map_name()
            if current is None:
                print(f"[GO_TO] Current map ID {self._map_id():#04x} not in world graph.")
                print(f"        game_state reports: {self.gs.map['map_name']}")
                return False

            if current == destination:
                print(f"[GO_TO] Already at {destination}.")
                return True

            route = self.graph.bfs_route(current, destination)
            if route is None:
                print(f"[GO_TO] No route from {current} to {destination}")
                return False

            print(f"[GO_TO] Route: {' → '.join(route)}")

            for i in range(len(route) - 1):
                src, dst = route[i], route[i + 1]
                print(f"[HOP]  {src} → {dst}")

                if not self._execute_hop(src, dst):
                    print(f"[ERROR] Failed on hop {src} → {dst}")
                    print(f"        Current position: {self._pos()}, map: {self._map_name()}")
                    return False

                # Let the game settle after each map transition
                for _ in range(30):
                    self.pyboy.tick()

            arrived = self._map_name() == destination
            if arrived:
                print(f"[GO_TO] Arrived at {destination}.")
            else:
                print(f"[GO_TO] Expected {destination}, currently at {self._map_name()}")
            return arrived

        except BattleInterrupt as exc:
            print(f"[GO_TO] Navigation suspended — battle started ({exc})")
            print(f"        Current position: {self._pos()}, map: {self._map_name()}")
            return False