"""
autonomous_controller/controller.py

AutonomousController — top-level navigation + action agent.

Architecture
------------
    NavCore       — bounded steps, live object occupancy, and input
    NavAstar      — full-map terrain A* with bounded dynamic replanning
    HopExecutor   — map-to-map hop execution (warps + connections)

Interrupt philosophy
--------------------
When an NPC interrupt fires mid-navigation (e.g. Prof. Oak stops the player),
the interrupt handler clears the dialogue and waits for the player's position
to stabilise within a frame budget. Displacement or timeout stops navigation.
The demo policy can auto-pick a starter when trapped in Oak's lab; otherwise
the caller decides the next action. BattleInterrupt propagates to the caller.

Starter picking
---------------
pick_starter(name) navigates to the selected ball and confirms the choice in
Oak's lab. It must only be called after Oak's
dialogue has fully ended and the lab is in a stable state.

Usage
-----
    controller = AutonomousController(pyboy, gs)

    # Navigation
    ok = controller.go_to("OAKS_LAB")
    if not ok:
        pass  # AI decides next step

    # Starter picking (after Oak's dialogue ends)
    controller.pick_starter("bulbasaur")
"""

from pyboy.utils import WindowEvent

from autonomous_controller.constants import DIRECTIONS
from autonomous_controller.hop_executor import HopExecutor
from autonomous_controller.interrupt_handler import (
    BattleInterrupt,
    ControlTimeout,
    InterruptHandler,
    StarterChoiceRequired,
)
from autonomous_controller.nav_astar import NavAstar
from autonomous_controller.nav_core import NavCore
from autonomous_controller.walkable_map import RomPassability
from autonomous_controller.world_graph import WorldGraph

# Walkable squares below the three starter balls in Oak's lab.
_STARTER_POSITIONS = {"bulbasaur": (8, 4), "charmander": (6, 4), "squirtle": (7, 4)}

# Species name as it appears in PokemonGameState.party_pokemon
_STARTER_SPECIES: dict[str, str] = {
    "bulbasaur": "BULBASAUR",
    "charmander": "CHARMANDER",
    "squirtle": "SQUIRTLE",
}


class AutonomousController(NavCore, NavAstar, HopExecutor):
    """High-level autonomous navigation controller for Pokemon Red."""

    def __init__(
        self,
        pyboy,
        game_state,
        bundle_path=None,
        starter: str | None = "charmander",
    ):
        super().__init__()
        self.pyboy = pyboy
        self.gs = game_state
        self.graph = WorldGraph(bundle_path)
        self.rom_pass = RomPassability(bundle_path)
        self.interrupt = InterruptHandler(pyboy, game_state)
        self.nav_stats = {"step_calls": 0, "blocked_steps": 0}
        self.last_error = ""

        # Legacy fixed-policy demos may auto-pick; None delegates the choice.
        self.starter = starter.lower() if starter else None

        self._expected_map_id: int = 0

        self._release_map = {
            WindowEvent.PRESS_ARROW_UP: WindowEvent.RELEASE_ARROW_UP,
            WindowEvent.PRESS_ARROW_DOWN: WindowEvent.RELEASE_ARROW_DOWN,
            WindowEvent.PRESS_ARROW_LEFT: WindowEvent.RELEASE_ARROW_LEFT,
            WindowEvent.PRESS_ARROW_RIGHT: WindowEvent.RELEASE_ARROW_RIGHT,
            WindowEvent.PRESS_BUTTON_A: WindowEvent.RELEASE_BUTTON_A,
            WindowEvent.PRESS_BUTTON_B: WindowEvent.RELEASE_BUTTON_B,
            WindowEvent.PRESS_BUTTON_START: WindowEvent.RELEASE_BUTTON_START,
        }

    # Navigation API

    def interact(self, object_slot: int) -> bool:
        """Approach a current-map sprite, face it, and start dialogue with A.

        Slots are map-local RAM identifiers, not NPC names. Recheck moving
        targets before confirming; never walk through a warp to approach one.
        Leave dialogue visible for the caller to read and advance explicitly.
        """
        if type(object_slot) is not int or not 1 <= object_slot <= 15:
            raise ValueError("Object slot must be between 1 and 15")
        self.last_error = ""
        if self.gs.map["in_battle"]:
            raise BattleInterrupt("Battle active before interaction")
        if self.gs.dialog.strip():
            self.last_error = "Advance the current dialogue before starting an interaction"
            return False
        origin = self._map_id()

        def target():
            return next((obj for obj in self.gs.map_objects if obj["slot"] == object_slot), None)

        initial = target()
        if initial is None:
            raise ValueError(f"No current-map object in slot {object_slot}")
        for _ in range(3):
            obj = target()
            if (
                self._map_id() != origin
                or obj is None
                or obj["picture_id"] != initial["picture_id"]
            ):
                self.last_error = "Map or target changed during interaction"
                return False
            forbidden = self._warp_tiles() | {(obj["x"], obj["y"])}
            candidates = []
            for direction, (dx, dy, _, _) in DIRECTIONS.items():
                for distance in (1, 2):
                    if distance == 2:
                        middle = (obj["x"] - dx, obj["y"] - dy)
                        counters = {self.gs.mem.read_byte(a) for a in range(0xD532, 0xD535)} - {255}
                        if self.rom_pass.tile(self._map_name(), *middle) not in counters:
                            continue
                    position = (obj["x"] - distance * dx, obj["y"] - distance * dy)
                    if position in forbidden:
                        continue
                    path = self._plan_path(position, forbidden)
                    if path is not None:
                        candidates.append((len(path), direction, position))
            if not candidates:
                self.last_error = "No reachable square beside the target"
                return False
            _, direction, position = min(candidates)
            if not self.navigate_to_tile(*position, max_steps=100, forbidden_tiles=forbidden):
                self.last_error = f"Could not approach target: {self.last_nav_reason}"
                return False
            current = target()
            if self._map_id() != origin:
                self.last_error = "Map changed during interaction"
                return False
            if current is None or not current["visible"]:
                self.last_error = "Target is no longer visible"
                return False
            if current["moving"] or (current["x"], current["y"]) != (obj["x"], obj["y"]):
                self.pyboy.tick(20)
                self.interrupt.check_and_handle()
                continue
            self.press(DIRECTIONS[direction][2])
            self.pyboy.tick(self.WALK_ANIMATION_FRAMES)
            current = target()
            if (
                self._map_id() != origin
                or self._pos() != position
                or current is None
                or not current["visible"]
                or current["moving"]
                or current["picture_id"] != initial["picture_id"]
                or (current["x"], current["y"]) != (obj["x"], obj["y"])
                or self.gs.map["player_facing"] != direction.upper()
            ):
                continue
            self.press(WindowEvent.PRESS_BUTTON_A)
            previous, stable = "", 0
            for _ in range(300):
                self.pyboy.tick()
                if self.gs.map["in_battle"]:
                    raise BattleInterrupt("Battle started by interaction")
                text = self.gs.dialog.strip()
                stable = stable + 1 if text and text == previous else 0
                previous = text
                if stable >= 20:
                    return True
                if self._map_id() != origin:
                    break
            if self._map_id() == origin and self.gs.dialog.strip():
                return True
            self.last_error = "Target did not open dialogue"
            return False
        self.last_error = "Target kept moving; interaction was not confirmed"
        return False

    def go_to(self, destination: str, _starter_done: bool = False) -> bool:
        """
        Navigate from the current map to ``destination`` via BFS + A*.

        Returns True on success.
        Returns False if:
          - no route exists
          - a hop fails (NPC displacement, etc.) — caller decides next step
          - control cannot be regained within the frame budget

        BattleInterrupt propagates when combat starts; it does not return False.

        Oak's lab fallback
        ------------------
        If a hop fails while the player is locked in OAKS_LAB with an empty
        party (Oak's cutscene just ended and a starter must be chosen),
        With starter=None, StarterChoiceRequired returns the decision to the caller.
        Otherwise pick_starter() is called automatically using ``self.starter``, and
        go_to() restarts from the new position.  This happens at most once
        per call chain (guarded by ``_starter_done``).
        """
        destination = destination.upper()
        self.last_error = ""

        try:
            if self.gs.map["in_battle"]:
                raise BattleInterrupt("Battle active before navigation")
            current = self._map_name()
            route = self.graph.terrain_route(
                current,
                destination,
                self._pos(),
                self.rom_pass,
                last_map=self.graph.map_name(self.gs.mem.read_byte(0xD365)),
            )

            if current is None or route is None:
                self.last_error = "No connected walking route from the current region."
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
                    current_map = self._map_name()
                    print(f"[ERROR] Hop {src} → {dst} failed at {current_map}")

                    # ── Oak's lab fallback ────────────────────────────────────
                    # If we end up locked in Oak's lab without a starter,
                    # pick one automatically and restart navigation.
                    if (
                        not _starter_done
                        and current_map == "OAKS_LAB"
                        and not self.gs.party_pokemon
                    ):
                        if self.starter is None:
                            if not self.interrupt.wait_for_control():
                                raise ControlTimeout("Timed out waiting for Oak's starter offer")
                            raise StarterChoiceRequired("Choose Bulbasaur, Charmander or Squirtle")
                        print(f"[GO_TO] Locked in Oak's lab — auto-picking {self.starter}…")
                        picked = self.pick_starter(self.starter)
                        if picked:
                            print("[GO_TO] Starter picked — resuming navigation.")
                            return self.go_to(destination, _starter_done=True)
                        else:
                            print("[GO_TO] pick_starter() failed — aborting.")
                            return False
                    # ── end fallback ──────────────────────────────────────────

                    print(f"        Current: {self._pos()}, map: {current_map}")
                    self.last_error = self.last_error or (
                        "A game script moved or stopped the player."
                        if self.interrupt.was_displaced
                        else "No reachable entrance or crossing; check objects and story gates."
                    )
                    return False  # AI agent decides what happens next

                # Settle after each map transition
                for _ in range(30):
                    self.pyboy.tick()

            arrived = self._map_name() == destination
            if arrived:
                print(f"[GO_TO] Arrived at {destination}.")
            else:
                print(f"[GO_TO] Expected {destination}, at {self._map_name()}")
            return arrived

        except ControlTimeout as exc:
            self.last_error = str(exc)
            return False
        except BattleInterrupt as exc:
            print(f"[GO_TO] Battle interrupt — navigation suspended. ({exc})")
            print(f"        Current: {self._pos()}, map: {self._map_name()}")
            raise

    # Starter picking

    def pick_starter(self, pokemon: str) -> bool:
        """
        Acquire a starter Pokemon from Oak's lab.
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
        if pokemon not in _STARTER_POSITIONS:
            raise ValueError(f"Unknown starter '{pokemon}'. Valid: {list(_STARTER_POSITIONS)}")

        if self.interrupt.is_interrupted():
            raise RuntimeError(
                "Cannot call pick_starter() while a dialogue or battle is active. "
                "Wait for the interrupt to clear first."
            )

        print(f"[STARTER] Picking {pokemon.title()}…")
        print("[STARTER] Waiting for Oak's sequence to finish before moving…")
        if not self.interrupt.wait_for_control():
            raise ControlTimeout("Timed out waiting for control before starter selection")

        # Approach from the current position, including a restored mid-lab checkpoint.
        if not self.navigate_to_tile(*_STARTER_POSITIONS[pokemon], max_steps=100):
            return False
        self.press(WindowEvent.PRESS_ARROW_UP)
        self.pyboy.tick(self.WALK_ANIMATION_FRAMES)

        # Spam A to confirm taking the Pokemon
        target = _STARTER_SPECIES[pokemon]
        print(f"[STARTER] Interacting with {pokemon.title()}'s pokeball (spamming A)…")

        for attempt in range(300):
            party = self.gs.party_pokemon
            if any(p.get("species_name", "").upper() == target for p in party):
                print(f"[STARTER] {pokemon.title()} acquired after {attempt} A press(es)!")
                if not self.interrupt.wait_for_control():
                    raise ControlTimeout("Timed out waiting for dialogue after starter selection")
                return True
            self.press(self.interrupt.dialogue_button())

        print(f"[STARTER] Timed out — {pokemon} not in party after 300 A presses.")
        return False
