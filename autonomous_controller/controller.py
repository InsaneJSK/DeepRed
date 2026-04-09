"""
autonomous_controller/controller.py

AutonomousController — top-level navigation + action agent.

Architecture
------------
    NavCore       — raw movement primitives (_step, _dodge, press, etc.)
    NavAstar      — A*-based navigate_to_tile with oscillation/escape
    HopExecutor   — map-to-map hop execution (warps + connections)

Interrupt philosophy
--------------------
When an NPC interrupt fires mid-navigation (e.g. Prof. Oak stops the player),
the interrupt handler clears the dialogue and waits for the player's position
to stabilise.  go_to() then returns False so the external AI agent can decide
what to do next (e.g. call pick_starter()).  Navigation is NOT automatically
re-tried after an interrupt — that is the AI agent's responsibility.

Starter picking
---------------
pick_starter(name) executes the hardcoded button sequence to acquire a
starter Pokemon from Oak's lab.  It must only be called after Oak's
dialogue has fully ended and the lab is in a stable state.

Usage
-----
    controller = AutonomousController(pyboy, gs, "world_graph.json")

    # Navigation
    ok = controller.go_to("OAKS_LAB")
    if not ok:
        pass  # AI decides next step

    # Starter picking (after Oak's dialogue ends)
    controller.pick_starter("bulbasaur")
"""

from pyboy.utils import WindowEvent  # pylint: disable=no-name-in-module

from autonomous_controller.world_graph       import WorldGraph
from autonomous_controller.walkable_map      import RomPassability
from autonomous_controller.interrupt_handler import InterruptHandler, BattleInterrupt
from autonomous_controller.nav_core          import NavCore
from autonomous_controller.nav_astar         import NavAstar
from autonomous_controller.hop_executor      import HopExecutor

# Starter-picking constants
# Steps to reach and face each starter's pokeball from the position where
# Oak's final pre-pick dialogue ends.
# 'face_up' = press UP to change facing without moving (table blocks movement)
_STARTER_STEPS: dict[str, list[str]] = {
    "bulbasaur":  ["down", "right", "right", "right", "face_up"],
    "charmander": ["down", "right", "face_up"],
    "squirtle":   ["down", "right", "right", "face_up"],
}

# Species name as it appears in PokemonGameState.party_pokemon
_STARTER_SPECIES: dict[str, str] = {
    "bulbasaur":  "BULBASAUR",
    "charmander": "CHARMANDER",
    "squirtle":   "SQUIRTLE",
}


class AutonomousController(NavCore, NavAstar, HopExecutor):  # pylint: disable=too-many-ancestors
    """High-level autonomous navigation controller for Pokemon Red."""

    def __init__(
        self,
        pyboy,
        game_state,
        graph_path: str,
        pokered_root: str = "pokered",
    ):
        super().__init__()
        self.pyboy     = pyboy
        self.gs        = game_state
        self.graph     = WorldGraph(graph_path)
        self.rom_pass  = RomPassability(pokered_root)
        self.interrupt = InterruptHandler(pyboy, game_state)

        self._expected_map_id: int = 0

        self._release_map = {
            WindowEvent.PRESS_ARROW_UP:     WindowEvent.RELEASE_ARROW_UP,
            WindowEvent.PRESS_ARROW_DOWN:   WindowEvent.RELEASE_ARROW_DOWN,
            WindowEvent.PRESS_ARROW_LEFT:   WindowEvent.RELEASE_ARROW_LEFT,
            WindowEvent.PRESS_ARROW_RIGHT:  WindowEvent.RELEASE_ARROW_RIGHT,
            WindowEvent.PRESS_BUTTON_A:     WindowEvent.RELEASE_BUTTON_A,
            WindowEvent.PRESS_BUTTON_B:     WindowEvent.RELEASE_BUTTON_B,
            WindowEvent.PRESS_BUTTON_START: WindowEvent.RELEASE_BUTTON_START,
        }

    # Navigation API

    def go_to(self, destination: str) -> bool:
        """
        Navigate from the current map to ``destination`` via BFS + A*.

        Returns True on success.
        Returns False if:
          - no route exists
          - a hop fails (including NPC displacement — caller decides next step)
          - a battle starts mid-navigation

        Navigation is NOT automatically re-tried after an interrupt.
        The AI agent is responsible for calling go_to() again (or pick_starter()
        or another action) once the interrupting event has resolved.
        """
        destination = destination.upper()

        try:
            current = self._map_name()
            route = self.graph.bfs_route(current, destination)

            if current is None or route is None:
                print(f"[GO_TO] No route from {current} to {destination}")
                return False

            if current == destination:
                print(f"[GO_TO] Already at {destination}.")
                return True

            print(f"[GO_TO] Route: {' → '.join(route)}")

            for i in range(len(route) - 1):
                src, dst = route[i], route[i + 1]
                print(f"[HOP]  {src} → {dst}")

                if not self._execute_hop(src, dst):
                    print(f"[ERROR] Hop {src} → {dst} failed")
                    print(f"        Current: {self._pos()}, map: {self._map_name()}")
                    return False   # AI agent decides what happens next

                # Settle after each map transition
                for _ in range(30):
                    self.pyboy.tick()

            arrived = self._map_name() == destination
            if arrived:
                print(f"[GO_TO] Arrived at {destination}.")
            else:
                print(f"[GO_TO] Expected {destination}, at {self._map_name()}")
            return arrived

        except BattleInterrupt as exc:
            # Destination reached just before battle started
            if self._map_name() == destination:
                print(f"[GO_TO] Arrived at {destination} (battle started on arrival).")
                return True
            print(f"[GO_TO] Battle interrupt — navigation suspended. ({exc})")
            print(f"        Current: {self._pos()}, map: {self._map_name()}")
            return False

    # Starter picking

    def pick_starter(self, pokemon: str) -> bool:
        """
        Acquire a starter Pokemon from Oak's lab.

        Must be called AFTER Oak's final pre-pick dialogue has fully ended
        and the lab is in a stable, non-interrupted state.

        Parameters
        ----------
        pokemon : "bulbasaur" | "charmander" | "squirtle"

        Raises
        ------
        ValueError
            Unknown pokemon name.
        RuntimeError
            Called while an interrupt (dialogue/battle) is active.

        Returns
        -------
        True if the pokemon was successfully added to the party,
        False if the A-press timeout was reached without confirmation.
        """
        pokemon = pokemon.lower().strip()
        if pokemon not in _STARTER_STEPS:
            raise ValueError(f"Unknown starter '{pokemon}'. Valid: {list(_STARTER_STEPS)}")

        if self.interrupt.is_interrupted():
            raise RuntimeError(
                "Cannot call pick_starter() while a dialogue or battle is active. "
                "Wait for the interrupt to clear first."
            )

        print(f"[STARTER] Picking {pokemon.title()}…")

        # Wait for Oak's entire multi-phase cutscene to complete.
        # The routine spams A through every dialogue box and waits for the
        # position to stabilise and CONTROL_GRACE consecutive dialog-free
        # frames before proceeding — covering the full sequence of:
        #   Oak triggers → player dragged to lab → Oak walks to table →
        #   "Choose your Pokemon!" → player regains control at bag screen.
        print("[STARTER] Waiting for Oak's sequence to finish before moving…")
        if not self.interrupt.wait_for_control():
            print("[STARTER] Timed out waiting for player control — giving up.")
            return False


        # Execute movement steps
        for step in _STARTER_STEPS[pokemon]:
            if step == "face_up":
                # Press UP briefly to change facing toward the pokeball table.
                # The table blocks movement, so this only changes the facing direction.
                self.pyboy.send_input(WindowEvent.PRESS_ARROW_UP)
                for _ in range(self.FRAMES_PER_STEP):
                    self.pyboy.tick()
                self.pyboy.send_input(WindowEvent.RELEASE_ARROW_UP)
                for _ in range(self.FRAMES_RELEASE):
                    self.pyboy.tick()
            else:
                if not self._step(step):
                    print(f"[STARTER] Warning: step '{step}' didn't move — continuing")

        # Spam A to confirm taking the Pokemon
        target = _STARTER_SPECIES[pokemon]
        print(f"[STARTER] Interacting with {pokemon.title()}'s pokeball (spamming A)…")

        for attempt in range(300):
            party = self.gs.party_pokemon
            if any(p.get("species_name", "").upper() == target for p in party):
                print(f"[STARTER] {pokemon.title()} acquired after {attempt} A press(es)!")
                return True
            self.press(WindowEvent.PRESS_BUTTON_A)

        print(f"[STARTER] Timed out — {pokemon} not in party after 300 A presses.")
        return False
