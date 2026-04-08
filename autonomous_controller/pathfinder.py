"""
autonomous_controller/pathfinder.py

A* tile-level pathfinder.  Used by NavAstar to plan routes within a single map.
"""

import heapq


def _heuristic(ax: int, ay: int, bx: int, by: int) -> int:
    return abs(ax - bx) + abs(ay - by)


def astar( #pylint: disable=too-many-arguments, too-many-locals, too-many-positional-arguments
    start_x: int, start_y: int,
    goal_x: int,  goal_y: int,
    is_passable_fn,
    max_steps: int = 2000,
) -> list[str] | None:
    """
    A* on a tile grid.

    is_passable_fn(x, y) -> bool
    Returns list of direction strings ("up"/"down"/"left"/"right"), or None
    if no path exists within max_steps node expansions.
    """
    from autonomous_controller.constants import DIRECTIONS  # local import avoids cycle #pylint: disable=import-outside-toplevel

    open_heap = [(_heuristic(start_x, start_y, goal_x, goal_y), 0, start_x, start_y, [])]
    visited: set[tuple[int, int]] = set()

    while open_heap:
        _, cost, x, y, path = heapq.heappop(open_heap)
        if (x, y) in visited:
            continue
        visited.add((x, y))

        if x == goal_x and y == goal_y:
            return path

        if cost >= max_steps:
            continue

        for direction, (dx, dy, _, _) in DIRECTIONS.items():
            nx, ny = x + dx, y + dy
            if (nx, ny) not in visited and is_passable_fn(nx, ny):
                new_cost = cost + 1
                priority = new_cost + _heuristic(nx, ny, goal_x, goal_y)
                heapq.heappush(open_heap, (priority, new_cost, nx, ny, path + [direction]))

    return None
