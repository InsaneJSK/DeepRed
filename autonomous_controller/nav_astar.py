"""
autonomous_controller/nav_astar.py

NavAstar mixin — A*-based tile navigation with oscillation detection,
escape bursts, and viewport-unreliable fallback.

Expects NavCore to be in the MRO (provides _step, _dodge, _pos, _map_id,
_build_passable_fn, _expected_map_id) and AutonomousController's graph/rom_pass.
"""

from autonomous_controller.constants import DIRECTIONS
from autonomous_controller.pathfinder import astar


class NavAstar:
    """Mixin: navigate_to_tile with oscillation/escape handling."""

    OSCILLATION_THRESHOLD = 3   # visits to same tile before triggering escape
    ESCAPE_BURST_STEPS    = 5   # forced steps during an escape burst

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
        else:
            vert  = ["up"    if dy <= 0 else "down",  "down" if dy <= 0 else "up"]
            horiz = ["right" if dx >= 0 else "left", "left" if dx >= 0 else "right"]
            return vert + horiz

    # ------------------------------------------------------------------
    # Main navigation
    # ------------------------------------------------------------------

    def navigate_to_tile(
        self,
        goal_x: int,
        goal_y: int,
        max_steps: int = 200,
        forbidden_tiles: set[tuple[int, int]] | None = None,
    ) -> bool:
        """
        Navigate to (goal_x, goal_y) using A* with live viewport collision.

        forbidden_tiles
            Set of (x, y) world tiles A* must not route through.  Use this to
            prevent the player from accidentally stepping into warp doors while
            navigating outdoors on a map that has buildings.

        Oscillation handling
            If the same tile is visited OSCILLATION_THRESHOLD times it is added
            to confirmed_blocked, forcing A* onto a different route.  When A*
            still can't find a path an ESCAPE_BURST_STEPS perpendicular burst
            physically moves the player out of the local minimum.

        Viewport-unreliable fallback
            If ALL four neighbours of the current position look like walls in the
            viewport (common in small indoor rooms where the camera offset maps
            tilemap data to the wrong location), we retry A* with only
            confirmed_blocked + forbidden_tiles as constraints and let _step
            discover actual walls organically.
        """
        confirmed_blocked: set[tuple[int, int]] = set()
        if forbidden_tiles is None:
            forbidden_tiles = set()
        visit_counts: dict[tuple[int, int], int] = {}

        def passable_check(passable_fn):
            def inner(wx, wy, _p=passable_fn, _b=confirmed_blocked, _f=forbidden_tiles):
                return (wx, wy) not in _b and (wx, wy) not in _f and _p(wx, wy)
            return inner

        for _ in range(max_steps):
            cx, cy = self._pos()
            print(f"  [NAV] Current position: ({cx},{cy}), goal: ({goal_x},{goal_y})")
            if cx == goal_x and cy == goal_y:
                return True

            if self._map_id() != self._expected_map_id:
                return False

            # ---- Oscillation detection ----
            visit_counts[(cx, cy)] = visit_counts.get((cx, cy), 0) + 1

            if len(confirmed_blocked) > 15:
                print(f"  [NAV] confirmed_blocked overflow ({len(confirmed_blocked)}) — resetting")
                confirmed_blocked.clear()
                visit_counts.clear()
                continue

            if visit_counts[(cx, cy)] >= self.OSCILLATION_THRESHOLD:
                print(f"  [NAV] Oscillation at ({cx},{cy}) (visited {visit_counts[(cx, cy)]}×) — forcing escape")
                confirmed_blocked.add((cx, cy))
                visit_counts.clear()

                escaped = False
                for esc_dir in self._escape_directions(cx, cy, goal_x, goal_y):
                    edx, edy, _, _ = DIRECTIONS[esc_dir]
                    steps_taken = 0
                    for _ in range(self.ESCAPE_BURST_STEPS):
                        if self._map_id() != self._expected_map_id:
                            return False
                        ex, ey = self._pos()
                        next_tile = (ex + edx, ey + edy)
                        if next_tile in forbidden_tiles or next_tile in confirmed_blocked:
                            break
                        if self._step(esc_dir):
                            steps_taken += 1
                        else:
                            break
                    if steps_taken > 0:
                        print(f"  [NAV] Escaped {steps_taken} step(s) {esc_dir}")
                        escaped = True
                        break

                if not escaped:
                    print(f"  [NAV] Escape failed — all non-forbidden directions blocked from ({cx},{cy})")
                    for ddx, ddy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
                        nb = (cx + ddx, cy + ddy)
                        if nb not in forbidden_tiles:
                            confirmed_blocked.add(nb)
                continue

            # ---- Normal A* step ----
            passable_fn = self._build_passable_fn()
            passable_with_memory = passable_check(passable_fn)

            effective_gx, effective_gy = goal_x, goal_y
            if not passable_with_memory(goal_x, goal_y):
                for ddx, ddy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
                    nx, ny = goal_x + ddx, goal_y + ddy
                    if passable_with_memory(nx, ny):
                        effective_gx, effective_gy = nx, ny
                        break

            path = astar(cx, cy, effective_gx, effective_gy, passable_with_memory)

            if path is None:
                # Viewport-unreliable fallback: if ALL neighbours look like walls,
                # the viewport data can't be trusted from here.  Retry with a
                # permissive function and let _step discover walls naturally.
                neighbours_ok = any(
                    passable_with_memory(cx + ddx, cy + ddy)
                    for _, (ddx, ddy, _, _) in DIRECTIONS.items()
                )
                if not neighbours_ok:
                    print(f"  [A*] Viewport unreliable at ({cx},{cy}) — retrying with permissive passability")
                    permissive = lambda wx, wy, _b=confirmed_blocked, _f=forbidden_tiles: (
                        (wx, wy) not in _b and (wx, wy) not in _f
                    )
                    path = astar(cx, cy, effective_gx, effective_gy, permissive)

            if path is None:
                print(f"  [A*] No path to ({effective_gx},{effective_gy}) from ({cx},{cy})")
                return False

            direction = path[0]
            ddx, ddy, _, _ = DIRECTIONS[direction]
            intended_x, intended_y = cx + ddx, cy + ddy

            moved = self._step(direction)

            if not moved:
                print(f"  [A*] Blocked at ({intended_x},{intended_y}), attempting dodge")
                dodged = self._dodge(direction, goal_x, goal_y)
                if not dodged:
                    print(f"  [A*] Dodge failed, marking ({intended_x},{intended_y}) blocked")
                    confirmed_blocked.add((intended_x, intended_y))

            nx, ny = self._pos()
            if (nx, ny) == (effective_gx, effective_gy) and (effective_gx, effective_gy) != (goal_x, goal_y):
                return True

        return self._pos() == (goal_x, goal_y)
