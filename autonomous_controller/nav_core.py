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

from autonomous_controller.constants import DIRECTIONS, OPPOSITE, ADDR_MAP_ID, ADDR_WARP_COUNT, ADDR_WARP_BASE
from pyboy.utils import WindowEvent  # pylint: disable=no-name-in-module


class NavCore:
    """Mixin: low-level movement + state-read primitives."""

    FRAMES_PER_STEP       = 2    # frames to hold a direction button per tile step
    FRAMES_RELEASE        = 1    # frames after release before next input
    WALK_ANIMATION_FRAMES = 17   # frames for a walk animation to complete
    WARP_WAIT_FRAMES      = 120  # max frames to wait for a warp transition

    # ------------------------------------------------------------------
    # Low-level input
    # ------------------------------------------------------------------

    def press(self, button, frames: int = 0) -> None:
        """Press and release a button.  Used for menus/dialogues, not movement."""
        frames = frames if frames != 0 else self.FRAMES_PER_STEP
        self.pyboy.send_input(button)
        for _ in range(frames):
            self.pyboy.tick()
        self.pyboy.send_input(self._release_map[button])
        for _ in range(self.FRAMES_RELEASE):
            self.pyboy.tick()

    # ------------------------------------------------------------------
    # State reads
    # ------------------------------------------------------------------

    def _pos(self) -> tuple[int, int]:
        """Current player tile position as (x, y) in player-step units."""
        m = self.gs.map
        return m["player_x"], m["player_y"]

    def _map_id(self) -> int:
        return self.mem.read_byte(ADDR_MAP_ID)

    def _map_name(self) -> str | None:
        """Current map as SCREAMING_SNAKE_CASE (matches WorldGraph keys)."""
        return self.graph.map_name(self._map_id())

    def _current_warps_from_ram(self) -> list[tuple[int, int, int, int]]:
        """
        Read live warp table from RAM.
        Returns list of (x, y, dest_map_id, dest_warp_idx).
        """
        count = self.mem.read_byte(ADDR_WARP_COUNT)
        warps = []
        for i in range(count):
            base = ADDR_WARP_BASE + i * 4
            wy = self.mem.read_byte(base)
            wx = self.mem.read_byte(base + 1)
            dest_warp = self.mem.read_byte(base + 2)
            dest_map  = self.mem.read_byte(base + 3)
            warps.append((wx, wy, dest_map, dest_warp))
        return warps

    # ------------------------------------------------------------------
    # Collision / viewport passability
    # ------------------------------------------------------------------

    def _build_passable_fn(self):
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
        scx = self.mem.read_byte(0xFF43)
        scy = self.mem.read_byte(0xFF42)

        vp_tile_x = scx // 8
        vp_tile_y = (scy // 8) if scy < 128 else (scy - 256) // 8

        collision = self.game.game_area_collision()  # [18 rows][20 cols]

        def is_passable(wx: int, wy: int) -> bool:
            tc = wx * 2 - vp_tile_x
            tr = wy * 2 - vp_tile_y
            if 0 <= tr < 18 and 0 <= tc < 20:
                return bool(collision[tr][tc])
            return True  # outside viewport — assume passable

        return is_passable

    # ------------------------------------------------------------------
    # Map transition wait
    # ------------------------------------------------------------------

    def _wait_for_map_change(self, expected_map_id: int, timeout: int = 0) -> bool:
        """Tick until map ID becomes expected_map_id or timeout. Returns True on success."""
        timeout = timeout if timeout != 0 else self.WARP_WAIT_FRAMES
        for _ in range(timeout):
            self.pyboy.tick()
            if self._map_id() == expected_map_id:
                return True
        return False

    # ------------------------------------------------------------------
    # Single-step movement with interrupt hook
    # ------------------------------------------------------------------

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
        x_before, y_before = self._pos()
        facing_before = self.gs.map["player_facing"]

        # Debug: log viewport and intended tile
        scx = self.mem.read_byte(0xFF43)
        scy = self.mem.read_byte(0xFF42)
        vp_x = scx // 8
        vp_y = scy // 8
        collision = self.game.game_area_collision()
        intended_col = (x_before + dx) - vp_x
        intended_row = (y_before + dy) - vp_y
        print(f"  [STEP] SCX={scx} SCY={scy} vp=({vp_x},{vp_y})")
        print(f"  [STEP] Intended world=({x_before+dx},{y_before+dy}) screen col={intended_col} row={intended_row}")
        if 0 <= intended_row < 18 and 0 <= intended_col < 20:
            print(f"  [STEP] collision[{intended_row}][{intended_col}] = {collision[intended_row][intended_col]}")

        # First attempt
        self.pyboy.send_input(press_ev)
        for _ in range(self.FRAMES_PER_STEP):
            self.pyboy.tick()
        self.pyboy.send_input(self._release_map[press_ev])
        for _ in range(self.FRAMES_RELEASE + self.WALK_ANIMATION_FRAMES):
            self.pyboy.tick()

        x_after, y_after = self._pos()
        facing_after = self.gs.map["player_facing"]

        if x_after == x_before + dx and y_after == y_before + dy:
            self.interrupt.check_and_handle()
            return True

        if x_after == x_before and y_after == y_before:
            # Retry once (handles facing-change-only first press)
            self.pyboy.send_input(press_ev)
            for _ in range(self.FRAMES_PER_STEP):
                self.pyboy.tick()
            self.pyboy.send_input(self._release_map[press_ev])
            for _ in range(self.FRAMES_RELEASE + self.WALK_ANIMATION_FRAMES):
                self.pyboy.tick()
            x_after, y_after = self._pos()
            facing_after = self.gs.map["player_facing"]
            moved = x_after == x_before + dx and y_after == y_before + dy
            if not moved:
                print(f"  [STEP] Still failed after retry: pos=({x_after},{y_after}) facing={facing_after}")
            self.interrupt.check_and_handle()
            return moved

        print(f"  [STEP] Wrong move: {direction} from ({x_before},{y_before}) "
              f"facing={facing_before} → ({x_after},{y_after}) facing={facing_after}")
        self.interrupt.check_and_handle()
        return False

    # ------------------------------------------------------------------
    # Dodge — perpendicular detour around an NPC/sprite obstacle
    # ------------------------------------------------------------------

    def _dodge(self, blocked_direction: str, goal_x: int, goal_y: int, max_dodge: int = 4) -> bool:
        """
        Try to navigate around a sprite obstacle by stepping perpendicular
        to the blocked direction (biased toward the goal) then retrying.

        Returns True if we successfully moved past the obstacle.
        """
        cx, cy = self._pos()
        dx_to_goal = goal_x - cx
        dy_to_goal = goal_y - cy

        if blocked_direction in ("left", "right"):
            perp_dirs = ["up", "down"] if dy_to_goal <= 0 else ["down", "up"]
        else:
            perp_dirs = ["right", "left"] if dx_to_goal >= 0 else ["left", "right"]

        for perp_dir in perp_dirs:
            for dodge_step in range(1, max_dodge + 1):
                moved = self._step(perp_dir)
                if not moved:
                    break
                retry_moved = self._step(blocked_direction)
                if retry_moved:
                    print(f"  [DODGE] Cleared obstacle after {dodge_step} {perp_dir} step(s)")
                    return True

            # Undo the perpendicular steps we took
            undo_dir = OPPOSITE[perp_dir]
            cx_now, cy_now = self._pos()
            if perp_dir in ("up", "down"):
                steps_taken = abs(cy_now - cy)
            else:
                steps_taken = abs(cx_now - cx)
            for _ in range(steps_taken):
                self._step(undo_dir)

        return False

    # ------------------------------------------------------------------
    # Direct walk — greedy fallback when A* can't plan a path
    # ------------------------------------------------------------------

    def _direct_walk(self, goal_x: int, goal_y: int, max_steps: int = 30) -> bool:
        """
        Fallback for tiny indoor maps: walk directly toward goal one step at a
        time without consulting collision.  Stops when goal is reached or the
        player stops moving (truly stuck).
        """
        print(f"  [DIRECT] Fallback direct walk to ({goal_x},{goal_y})")
        for _ in range(max_steps):
            cx, cy = self._pos()
            if cx == goal_x and cy == goal_y:
                return True
            dx = goal_x - cx
            dy = goal_y - cy
            direction = ("right" if dx > 0 else "left") if abs(dx) >= abs(dy) \
                        else ("down" if dy > 0 else "up")
            prev = self._pos()
            self._step(direction)
            if self._pos() == prev:
                alt = ("down" if dy > 0 else "up") if dy != 0 else ("right" if dx > 0 else "left")
                self._step(alt)
        return self._pos() == (goal_x, goal_y)
