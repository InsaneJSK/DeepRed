"""
autonomous_controller/nav_core.py

NavCore mixin — low-level movement primitives for AutonomousController.

Provides: press, _step (with interrupt hook), _dodge, _direct_walk,
          _build_passable_fn, _pos, _map_id, _map_name,
          _current_warps_from_ram, _wait_for_map_change.

Expects the subclass to supply:
    self.pyboy      — PyBoy instance
    self.gs         — PokemonGameState
    self.mem        — MemoryReader
    self.game       — pyboy.game_wrapper
    self.interrupt  — InterruptHandler
    self._release_map — dict mapping press events to release events
    self._expected_map_id — int (current expected map id)
"""

from typing import Any, Protocol, Callable
from autonomous_controller.constants import (DIRECTIONS, OPPOSITE,
                                             ADDR_MAP_ID, ADDR_WARP_COUNT, ADDR_WARP_BASE)

class _NavCoreDeps(Protocol): #pylint: disable=too-few-public-methods
    pyboy: Any
    gs: Any
    interrupt: Any
    graph: Any
    _release_map: dict

class NavCore(_NavCoreDeps): #pylint: disable=too-few-public-methods
    """Mixin: low-level movement + state-read primitives."""

    FRAMES_PER_STEP       = 2    # frames to hold a direction button per tile step
    FRAMES_RELEASE        = 1    # frames after release before next input
    WALK_ANIMATION_FRAMES = 17   # frames for a walk animation to complete
    WARP_WAIT_FRAMES      = 120  # max frames to wait for a warp transition

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    # Low-level input

    def press(self, button, frames: int = 0) -> None:
        """Press and release a button.  Used for menus/dialogues, not movement."""
        frames = frames if frames != 0 else self.FRAMES_PER_STEP
        self.pyboy.send_input(button)
        for _ in range(frames):
            self.pyboy.tick()
        self.pyboy.send_input(self._release_map[button])
        for _ in range(self.FRAMES_RELEASE):
            self.pyboy.tick()

    # State reads

    def _pos(self) -> tuple[int, int]:
        """Current player tile position as (x, y) in player-step units."""
        m = self.gs.map
        return m["player_x"], m["player_y"]

    def _map_id(self) -> int:
        return self.gs.mem.read_byte(ADDR_MAP_ID)

    def _map_name(self) -> str | None:
        """Current map as SCREAMING_SNAKE_CASE (matches WorldGraph keys)."""
        return self.graph.map_name(self._map_id())

    def _current_warps_from_ram(self) -> list[tuple[int, int, int, int]]:
        """
        Read live warp table from RAM.
        Returns list of (x, y, dest_map_id, dest_warp_idx).
        """
        count = self.gs.mem.read_byte(ADDR_WARP_COUNT)
        warps = []
        for i in range(count):
            base = ADDR_WARP_BASE + i * 4
            wy = self.gs.mem.read_byte(base)
            wx = self.gs.mem.read_byte(base + 1)
            dest_warp = self.gs.mem.read_byte(base + 2)
            dest_map  = self.gs.mem.read_byte(base + 3)
            warps.append((wx, wy, dest_map, dest_warp))
        return warps

    # Collision / viewport passability

    def _build_passable_fn(self) -> Callable[[int, int], bool]:
        """
        Returns is_passable(world_x, world_y) -> bool.

        Player x/y are in player-step units (16 px = 2 tiles).
        game_area_collision() returns an 18×20 matrix in 8-px tile units.

        Correct mapping:
            tc = wx * 2 - (SCX // 8)
            tr = wy * 2 - signed(SCY // 8)

        We sample the top-left 8-px tile of each 2×2 player-step block.
        Tiles outside the viewport are assumed passable (optimistic; A* learns
        the real state when the player tries to step there).
        """
        scx = self.gs.mem.read_byte(0xFF43)
        scy = self.gs.mem.read_byte(0xFF42)

        vp_tile_x = scx // 8
        vp_tile_y = (scy // 8) if scy < 128 else (scy - 256) // 8

        collision = self.pyboy.game_wrapper.game_area_collision()  # [18 rows][20 cols]

        def is_passable(wx: int, wy: int) -> bool:
            tc = wx * 2 - vp_tile_x
            tr = wy * 2 - vp_tile_y
            if 0 <= tr < 18 and 0 <= tc < 20:
                return bool(collision[tr][tc])
            return True  # outside viewport — assume passable

        return is_passable

    # Map transition wait

    def _wait_for_map_change(self, expected_map_id: int, timeout: int = 0) -> bool:
        """Tick until map ID becomes expected_map_id or timeout. Returns True on success."""
        timeout = timeout if timeout != 0 else self.WARP_WAIT_FRAMES
        for _ in range(timeout):
            self.pyboy.tick()
            if self._map_id() == expected_map_id:
                return True
        return False

    # Single-step movement with interrupt hook

    def _debug(self, dx: int, dy: int, pos_before: tuple[int, int, int]) -> None:
        # Debug: log viewport and intended tile
        scx = self.gs.mem.read_byte(0xFF43)
        scy = self.gs.mem.read_byte(0xFF42)
        vp_x = scx // 8
        vp_y = scy // 8
        collision = self.pyboy.game_wrapper.game_area_collision()
        intended_col = (pos_before[0] + dx) - vp_x
        intended_row = (pos_before[1] + dy) - vp_y
        print(f"  [STEP] SCX={scx} SCY={scy} vp=({vp_x},{vp_y})")
        print(f"  [STEP] Intended world=({pos_before[0]+dx},{pos_before[1]+dy})\
               screen col={intended_col} row={intended_row}")
        if 0 <= intended_row < 18 and 0 <= intended_col < 20:
            print(f"  [STEP] collision[{intended_row}][{intended_col}]\
                   = {collision[intended_row][intended_col]}")

    def _step(self, direction: str) -> bool:
        """
        Press the direction button, wait for the walk animation, confirm position.

        After movement confirmation, calls self.interrupt.check_and_handle().
        This catches NPC dialogue or battles that start as a result of stepping
        onto a trigger tile.

        Returns True if the player moved exactly one step in the expected direction.

        Raises
        ------
        BattleInterrupt
            Propagated from InterruptHandler when a battle starts.
        """
        dx, dy, press_ev, _ = DIRECTIONS[direction]
        pos_before = (*self._pos(), self.gs.map["player_facing"])
        self._debug(dx, dy, pos_before)
        # First attempt
        self.pyboy.send_input(press_ev)
        for _ in range(self.FRAMES_PER_STEP):
            self.pyboy.tick()
        self.pyboy.send_input(self._release_map[press_ev])
        for _ in range(self.FRAMES_RELEASE + self.WALK_ANIMATION_FRAMES):
            self.pyboy.tick()

        x_after, y_after = self._pos()
        facing_after = self.gs.map["player_facing"]

        if x_after == pos_before[0] + dx and y_after == pos_before[1] + dy:
            self.interrupt.check_and_handle()
            return True

        if x_after == pos_before[0] and y_after == pos_before[1]:
            # Retry once (handles facing-change-only first press)
            self.pyboy.send_input(press_ev)
            for _ in range(self.FRAMES_PER_STEP):
                self.pyboy.tick()
            self.pyboy.send_input(self._release_map[press_ev])
            for _ in range(self.FRAMES_RELEASE + self.WALK_ANIMATION_FRAMES):
                self.pyboy.tick()
            x_after, y_after = self._pos()
            facing_after = self.gs.map["player_facing"]
            moved = x_after == pos_before[0] + dx and y_after == pos_before[1] + dy
            if not moved:
                print(f"[STEP] Still failed after retry: pos=({x_after},{y_after})\
                       facing={facing_after}")
            self.interrupt.check_and_handle()
            return moved

        print(f"  [STEP] Wrong move: {direction} from ({pos_before[0]},{pos_before[1]}) "
              f"facing={pos_before[2]} → ({x_after},{y_after}) facing={facing_after}")
        self.interrupt.check_and_handle()
        return False

    # Dodge helpers

    def _get_perpendicular_dirs(self, blocked_direction, pos, goal):
        cx, cy = pos
        goal_x, goal_y = goal
        dx_to_goal = goal_x - cx
        dy_to_goal = goal_y - cy

        if blocked_direction in ("left", "right"):
            return ["up", "down"] if dy_to_goal <= 0 else ["down", "up"]

        return ["right", "left"] if dx_to_goal >= 0 else ["left", "right"]

    def _try_dodge_direction(self, perp_dir, blocked_direction, max_dodge):
        steps_taken = 0

        for step_count in range(1, max_dodge + 1):
            if not self._step(perp_dir):
                break

            steps_taken += 1

            if self._step(blocked_direction):
                print(f"  [DODGE] Cleared obstacle after {step_count} {perp_dir} step(s)")
                return True

        self._undo_dodge(perp_dir, steps_taken)
        return False

    def _undo_dodge(self, perp_dir, steps_taken):
        undo_dir = OPPOSITE[perp_dir]

        for _ in range(steps_taken):
            self._step(undo_dir)

    # Dodge — perpendicular detour around an NPC/sprite obstacle

    def _dodge(self, blocked_direction: str, goal_x: int, goal_y: int, max_dodge: int = 4) -> bool:
        cx, cy = self._pos()

        perp_dirs = self._get_perpendicular_dirs(
            blocked_direction, (cx, cy), (goal_x, goal_y)
        )

        for perp_dir in perp_dirs:
            if self._try_dodge_direction(perp_dir, blocked_direction, max_dodge):
                return True

        return False
