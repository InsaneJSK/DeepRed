"""
autonomous_controller/nav_astar.py

NavAstar mixin — A*-based tile navigation with oscillation detection,
escape bursts, and viewport-unreliable fallback.

Expects NavCore to be in the MRO (provides _step, _dodge, _pos, _map_id,
_build_passable_fn, _expected_map_id) and AutonomousController's graph/rom_pass.
"""

from typing import Protocol, Callable

from autonomous_controller.constants import DIRECTIONS
from autonomous_controller.pathfinder import astar

class _NavAstarDeps(Protocol): #pylint: disable=too-few-public-methods
    def _step(self, direction: str) -> bool: ...
    def _dodge(self, direction: str, goal_x: int, goal_y: int) -> bool: ...
    def _pos(self) -> tuple[int, int]: ...
    def _map_id(self) -> int: ...
    def _build_passable_fn(self) -> Callable[[int, int], bool]: ...
    _expected_map_id: int

class NavAstar(_NavAstarDeps): #pylint: disable=too-few-public-methods
    """Mixin: navigate_to_tile with oscillation/escape handling."""

    OSCILLATION_THRESHOLD = 3   # visits to same tile before triggering escape
    ESCAPE_BURST_STEPS    = 5   # forced steps during an escape burst

    # Helpers

    def _escape_directions(self, cx: int, cy: int, goal_x: int, goal_y: int) -> list[str]:
        """
        Return directions to try during a forced escape.

        Breaks out PERPENDICULAR to the dominant goal axis first so the player
        sidesteps the local obstacle cluster rather than pushing against it.
        """
        dx = goal_x - cx
        dy = goal_y - cy
        if abs(dy) >= abs(dx):
            horiz = ["right" if dx >= 0 else "left", "left" if dx >= 0 else "right"]
            vert  = ["up"    if dy <= 0 else "down",  "down" if dy <= 0 else "up"]
            return horiz + vert

        vert  = ["up"    if dy <= 0 else "down",  "down" if dy <= 0 else "up"]
        horiz = ["right" if dx >= 0 else "left", "left" if dx >= 0 else "right"]
        return vert + horiz

    # Navigation: Helper functions

    def _reached_goal(self, cx, cy, gx, gy):
        return cx == gx and cy == gy

    def _map_changed(self):
        return self._map_id() != self._expected_map_id

    def _escape(self, cx, cy, ctx) -> bool:
        gx = ctx["goal_x"]
        gy = ctx["goal_y"]
        confirmed_blocked = ctx["confirmed_blocked"]
        forbidden_tiles = ctx["forbidden_tiles"]

        for esc_dir in self._escape_directions(cx, cy, gx, gy):
            edx, edy, _, _ = DIRECTIONS[esc_dir]
            steps_taken = 0

            for _ in range(self.ESCAPE_BURST_STEPS):
                if self._map_changed():
                    return False

                ex, ey = self._pos() #pylint: disable=assignment-from-no-return, disable=unpacking-non-sequence
                next_tile = (ex + edx, ey + edy)

                if next_tile in forbidden_tiles or next_tile in confirmed_blocked:
                    break

                if self._step(esc_dir):
                    steps_taken += 1
                else:
                    break

            if steps_taken > 0:
                return True

        return False

    def _handle_oscillation(
        self, cx, cy, ctx,
        visit_counts) -> bool:
        visit_counts[(cx, cy)] = visit_counts.get((cx, cy), 0) + 1
        confirmed_blocked = ctx["confirmed_blocked"]
        forbidden_tiles = ctx["forbidden_tiles"]
        gx = ctx["goal_x"]
        gy = ctx["goal_y"]

        if len(confirmed_blocked) > 15:
            confirmed_blocked.clear()
            visit_counts.clear()
            return True

        if visit_counts[(cx, cy)] < self.OSCILLATION_THRESHOLD:
            return False

        confirmed_blocked.add((cx, cy))
        visit_counts.clear()
        ctx = {
            "goal_x": gx,
            "goal_y": gy,
            "confirmed_blocked": confirmed_blocked,
            "forbidden_tiles": forbidden_tiles,
        }
        if self._escape(cx, cy, ctx):
            return True

        # fallback: block neighbors
        for ddx, ddy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
            nb = (cx + ddx, cy + ddy)
            if nb not in forbidden_tiles:
                confirmed_blocked.add(nb)

        return True

    def _compute_path(self, cx, cy, ctx):
        confirmed_blocked = ctx["confirmed_blocked"]
        forbidden_tiles = ctx["forbidden_tiles"]
        gx = ctx["goal_x"]
        gy = ctx["goal_y"]
        passable_fn: Callable[[int, int], bool] = self._build_passable_fn()

        def passable(wx, wy):
            return (
                (wx, wy) not in confirmed_blocked and
                (wx, wy) not in forbidden_tiles and
                passable_fn(wx, wy) #pylint: disable=not-callable
            )

        effective_gx, effective_gy = self._adjust_goal(gx, gy, passable)

        path = astar(cx, cy, effective_gx, effective_gy, passable)

        if path is None and self._viewport_unreliable(cx, cy, passable):
            ctx = {
                "goal_x": effective_gx,
                "goal_y": effective_gy,
                "confirmed_blocked": confirmed_blocked,
                "forbidden_tiles": forbidden_tiles,
            }
            path = self._retry_with_permissive(cx, cy, ctx)

        if path is None:
            return None, (effective_gx, effective_gy)

        return path, (effective_gx, effective_gy)

    def _adjust_goal(self, gx, gy, passable):
        if passable(gx, gy):
            return gx, gy

        for ddx, ddy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
            nx, ny = gx + ddx, gy + ddy
            if passable(nx, ny):
                return nx, ny

        return gx, gy

    def _viewport_unreliable(self, cx, cy, passable):
        return not any(
            passable(cx + ddx, cy + ddy)
            for _, (ddx, ddy, _, _) in DIRECTIONS.items()
        )

    def _retry_with_permissive(self, cx, cy, ctx):
        confirmed_blocked = ctx["confirmed_blocked"]
        forbidden_tiles = ctx["forbidden_tiles"]
        gx = ctx["goal_x"]
        gy = ctx["goal_y"]
        def permissive(wx, wy):
            return (
                (wx, wy) not in confirmed_blocked and
                (wx, wy) not in forbidden_tiles
            )

        return astar(cx, cy, gx, gy, permissive)

    def _execute_step(self, direction, gx, gy,
                  confirmed_blocked) -> bool:
        cx, cy = self._pos() #pylint: disable=assignment-from-no-return, disable=unpacking-non-sequence
        ddx, ddy, _, _ = DIRECTIONS[direction]
        intended = (cx + ddx, cy + ddy)

        moved = self._step(direction) #pylint: disable=assignment-from-no-return

        if moved:
            return False

        if not self._dodge(direction, gx, gy):
            confirmed_blocked.add(intended)

        return True

    def _reached_adjacent_goal(self, pos, effective_goal, real_goal_x, real_goal_y):
        return (
            pos == effective_goal and
            effective_goal != (real_goal_x, real_goal_y)
        )

    # Main navigation

    def navigate_to_tile(
        self,
        goal_x: int,
        goal_y: int,
        max_steps: int = 200,
        forbidden_tiles: set[tuple[int, int]] | None = None,
    ) -> bool:
        """Navigate to the specified tile using A* with oscillation and escape handling."""
        confirmed_blocked: set[tuple[int, int]] = set()
        forbidden_tiles = forbidden_tiles or set()
        ctx = {
            "goal_x": goal_x,
            "goal_y": goal_y,
            "confirmed_blocked": confirmed_blocked,
            "forbidden_tiles": forbidden_tiles,
        }
        visit_counts: dict[tuple[int, int], int] = {}

        for _ in range(max_steps):
            cx, cy = self._pos() #pylint: disable=assignment-from-no-return, disable=unpacking-non-sequence

            if self._reached_goal(cx, cy, goal_x, goal_y):
                return True

            if self._map_changed():
                return False

            if self._handle_oscillation(cx, cy, ctx,
                                    visit_counts):
                continue

            path, effective_goal = self._compute_path(cx, cy, ctx)

            if path is None:
                return False

            if self._execute_step(path[0], goal_x, goal_y,
                                confirmed_blocked):
                continue

            if self._reached_adjacent_goal((cx, cy), effective_goal, goal_x, goal_y):
                return True

        return self._pos() == (goal_x, goal_y)
