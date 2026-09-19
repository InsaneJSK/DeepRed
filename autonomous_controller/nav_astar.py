"""Terrain navigation with failed-edge learning and refreshed live NPC avoidance."""

from autonomous_controller.constants import DIRECTIONS
from autonomous_controller.pathfinder import astar


class NavAstar:
    def _plan_path(self, goal, forbidden=(), failed=()):
        name, start = self._map_name(), self._pos()
        occupied = self._occupied_tiles()

        def passable(x, y):
            return (
                (x, y) not in forbidden
                and (x, y) not in occupied
                and self.rom_pass.is_passable(name, x, y)
            )

        def edge(x, y, nx, ny):
            return (x, y, nx, ny) not in failed and self.rom_pass.can_step(name, x, y, nx, ny)

        return astar(*start, *goal, passable, can_step=edge)

    def navigate_to_tile(self, goal_x, goal_y, max_steps=500, forbidden_tiles=None):
        """Reach exactly this square. Map changes and scripted displacements stop us.

        Failed movement excludes an edge, rather than launching a blind dodge.
        NPCs get a bounded wait/replan window. The old straight-line reachability
        cache is not used: reaching a goal never proves a safe path from elsewhere.
        """
        origin = self._map_id()
        goal = (goal_x, goal_y)
        forbidden = set(forbidden_tiles or ())
        failed, waits = set(), 0
        self.last_nav_reason = ""
        for _ in range(max_steps):
            if self._map_id() != origin:
                self.last_nav_reason = "map_changed"
                return False
            start = self._pos()
            if start == goal:
                return True
            path = self._plan_path(goal, forbidden, failed)
            if not path:
                if waits < 2:
                    waits += 1
                    self.pyboy.tick(30)
                    self.interrupt.check_and_handle()
                    if getattr(self.interrupt, "was_displaced", False):
                        self.last_nav_reason = "script_displacement"
                        return False
                    failed.clear()
                    continue
                self.last_nav_reason = "no_path"
                return False
            direction = path[0]
            dx, dy, _, _ = DIRECTIONS[direction]
            moved = self._step(direction)
            if self._map_id() != origin:
                self.last_nav_reason = "map_changed"
                return False
            if self.interrupt.was_displaced:
                self.last_nav_reason = "script_displacement"
                return False
            if not moved:
                failed.add((*start, start[0] + dx, start[1] + dy))
        self.last_nav_reason = "step_limit"
        return False
