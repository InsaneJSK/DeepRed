"""Bounded A* with parent pointers and directional edge constraints."""

import heapq

from autonomous_controller.constants import DIRECTIONS


def astar(start_x, start_y, goal_x, goal_y, is_passable_fn, max_steps=20000, can_step=None):
    start, goal = (start_x, start_y), (goal_x, goal_y)
    if start == goal:
        return []
    if not is_passable_fn(*goal):
        return None

    def heuristic(pos):
        return abs(pos[0] - goal_x) + abs(pos[1] - goal_y)

    queue = [(heuristic(start), 0, start)]
    costs, parents = {start: 0}, {}
    expanded = 0
    while queue and expanded < max_steps:
        _, cost, pos = heapq.heappop(queue)
        if costs.get(pos) != cost:
            continue
        expanded += 1
        if pos == goal:
            path = []
            while pos != start:
                pos, direction = parents[pos]
                path.append(direction)
            return path[::-1]
        for direction, (dx, dy, _, _) in DIRECTIONS.items():
            nxt = (pos[0] + dx, pos[1] + dy)
            if not is_passable_fn(*nxt) or (can_step and not can_step(*pos, *nxt)):
                continue
            new_cost = cost + 1
            if new_cost < costs.get(nxt, float("inf")):
                costs[nxt] = new_cost
                parents[nxt] = (pos, direction)
                heapq.heappush(queue, (new_cost + heuristic(nxt), new_cost, nxt))
    return None
