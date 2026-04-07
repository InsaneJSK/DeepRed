"""
autonomous_controller/controller.py

Autonomous navigation controller for Pokemon Red.
Uses PokemonGameState for all memory reads (no duplicate RAM address definitions).
Uses world_graph.json for inter-map BFS routing + A* for tile-level pathfinding.

Usage:
    from memory_state.game_state import PokemonGameState
    from autonomous_controller.controller import AutonomousController

    game_state = PokemonGameState(pyboy)
    controller = AutonomousController(pyboy, game_state, "world_graph.json")
    controller.go_to("VIRIDIAN_FOREST")
"""

import json
import heapq
from collections import deque
from pyboy.utils import WindowEvent  # pylint: disable=no-name-in-module

# ---------------------------------------------------------------------------
# RAM addresses only used here (not in game_state.py)
# ---------------------------------------------------------------------------
ADDR_WARP_COUNT = 0xD3AE        # Number of warps on current map
ADDR_WARP_BASE  = 0xD3AF        # Warp table: 4 bytes each (y, x, dest_warp_idx, dest_map_id)
ADDR_MAP_ID     = 0xD35E        # Current map ID

# ---------------------------------------------------------------------------
# Direction vocabulary — up/down/left/right everywhere, no compass directions
# ---------------------------------------------------------------------------
# dx, dy, press event, release event
DIRECTIONS = {
    "up":    ( 0, -1, WindowEvent.PRESS_ARROW_UP,    WindowEvent.RELEASE_ARROW_UP),
    "down":  ( 0,  1, WindowEvent.PRESS_ARROW_DOWN,  WindowEvent.RELEASE_ARROW_DOWN),
    "left":  (-1,  0, WindowEvent.PRESS_ARROW_LEFT,  WindowEvent.RELEASE_ARROW_LEFT),
    "right": ( 1,  0, WindowEvent.PRESS_ARROW_RIGHT, WindowEvent.RELEASE_ARROW_RIGHT),
}

OPPOSITE = {"up": "down", "down": "up", "left": "right", "right": "left"}

# pokered connection headers use compass — translate once at the graph boundary
COMPASS_TO_ARROW = {
    "north": "up",
    "south": "down",
    "west":  "left",
    "east":  "right",
}


# ---------------------------------------------------------------------------
# World Graph
# ---------------------------------------------------------------------------

class WorldGraph:
    def __init__(self, graph_path: str):
        with open(graph_path, encoding="utf-8") as f:
            data = json.load(f)
        self.maps: dict = data["maps"]
        self.name_to_id: dict[str, int] = data["map_name_to_id"]
        self.id_to_name: dict[int, str] = {
            int(k): v for k, v in data["map_id_to_name"].items()
        }
        self._expected_map_id = 0

    def map_name(self, map_id: int) -> str | None:
        return self.id_to_name.get(map_id)

    def map_id(self, map_name: str) -> int | None:
        return self.name_to_id.get(map_name.upper())

    def warps(self, map_name: str) -> list[dict]:
        return self.maps.get(map_name.upper(), {}).get("warps", [])

    def connections(self, map_name: str) -> dict:
        return self.maps.get(map_name.upper(), {}).get("connections", {})

    def neighbors(self, map_name: str) -> list[str]:
        result = []
        for warp in self.warps(map_name):
            result.append(warp["dest_map"])
        for conn in self.connections(map_name).values():
            result.append(conn["map"])
        return result

    def bfs_route(self, src: str, dst: str) -> list[str] | None:
        """BFS over map graph. Returns map name sequence src→dst inclusive."""
        src, dst = src.upper(), dst.upper()
        if src == dst:
            return [src]
        visited = {src}
        queue = deque([[src]])
        while queue:
            path = queue.popleft()
            current = path[-1]
            for neighbor in self.neighbors(current):
                if neighbor not in visited:
                    new_path = path + [neighbor]
                    if neighbor == dst:
                        return new_path
                    visited.add(neighbor)
                    queue.append(new_path)
        return None


# ---------------------------------------------------------------------------
# A* tile pathfinder
# ---------------------------------------------------------------------------

def _heuristic(ax, ay, bx, by) -> int:
    return abs(ax - bx) + abs(ay - by)


def astar(
    start_x: int, start_y: int,
    goal_x: int,  goal_y: int,
    is_passable_fn,
    max_steps: int = 2000,
) -> list[str] | None:
    """
    A* on a tile grid.
    is_passable_fn(x, y) -> bool
    Returns list of direction strings ("up"/"down"/"left"/"right"), or None.
    """
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


# ---------------------------------------------------------------------------
# AutonomousController
# ---------------------------------------------------------------------------

class AutonomousController:
    """
    High-level autonomous controller.
    All game state is read through PokemonGameState — no duplicate RAM reads.
    """

    FRAMES_PER_STEP  = 2   # frames to hold a direction button per tile step
    FRAMES_RELEASE   = 1    # frames after release before next input
    WALK_ANIMATION_FRAMES = 17   # frames it takes for a walk animation to complete
    WARP_WAIT_FRAMES = 120  # max frames to wait for a warp transition

    def __init__(self, pyboy, game_state, graph_path: str):
        """
        pyboy:      PyBoy instance
        game_state: PokemonGameState instance (from memory_state.game_state)
        graph_path: path to world_graph.json
        """
        self.pyboy = pyboy
        self.gs    = game_state       # PokemonGameState
        self.mem   = game_state.mem   # MemoryReader (reuse, don't duplicate)
        self.graph = WorldGraph(graph_path)
        self.game  = pyboy.game_wrapper
        self._expected_map_id = 0

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
    # Low-level input
    # ------------------------------------------------------------------

    def press(self, button, frames: int = 0):
        """Press and release a button. For menus/dialogues, not movement."""
        frames = frames if frames != 0 else self.FRAMES_PER_STEP
        self.pyboy.send_input(button)
        for _ in range(frames):
            self.pyboy.tick()
        self.pyboy.send_input(self._release_map[button])
        for _ in range(self.FRAMES_RELEASE):
            self.pyboy.tick()
    # def press(self, button, frames: int = 0):
    #     """Press and release a button, ticking PyBoy throughout."""
    #     frames = frames if frames != 0 else self.FRAMES_PER_STEP
    #     #press
    #     self.pyboy.send_input(button)
    #     for _ in range(frames):
    #         self.pyboy.tick()
    #     #release
    #     self.pyboy.send_input(self._release_map[button])
    #     for _ in range(self.FRAMES_RELEASE):
    #         self.pyboy.tick()
    #     #wait
    #     for _ in range(self.WALK_ANIMATION_FRAMES):
    #         self.pyboy.tick()

    # ------------------------------------------------------------------
    # State reads — via PokemonGameState where possible
    # ------------------------------------------------------------------

    def _pos(self) -> tuple[int, int]:
        """
        Current player tile position as (x, y).
        Reads from game_state.map which uses your verified RAM addresses.
        """
        m = self.gs.map
        return m["player_x"], m["player_y"]

    def _map_id(self) -> int:
        return self.mem.read_byte(ADDR_MAP_ID)

    def _map_name(self) -> str | None:
        """
        Current map as SCREAMING_SNAKE_CASE for graph lookups.
        NOTE: game_state.map["map_name"] returns Title Case with spaces,
        which doesn't match graph keys. We go through WorldGraph.map_name()
        instead, which uses the id_to_name dict built from map_constants.asm.
        """
        return self.graph.map_name(self._map_id())

    def _current_warps_from_ram(self) -> list[tuple[int, int, int, int]]:
        """
        Read live warp table from RAM.
        Returns list of (x, y, dest_map_id, dest_warp_idx).
        Useful for sanity-checking that we're standing on a warp tile.
        """
        count = self.mem.read_byte(ADDR_WARP_COUNT)
        warps = []
        for i in range(count):
            base = ADDR_WARP_BASE + i * 4
            wy        = self.mem.read_byte(base)
            wx        = self.mem.read_byte(base + 1)
            dest_warp = self.mem.read_byte(base + 2)
            dest_map  = self.mem.read_byte(base + 3)
            warps.append((wx, wy, dest_map, dest_warp))
        return warps

    # ------------------------------------------------------------------
    # Collision
    # ------------------------------------------------------------------

    def _build_passable_fn(self):
        """
        Returns is_passable(world_x, world_y) -> bool.

        Uses PyBoy's game_area_collision() (20x18 screen tiles) mapped to
        world coordinates via the VRAM scroll registers SCX/SCY.
        Tiles outside the current viewport are conservatively impassable.
        """
        
        scx = self.mem.read_byte(0xFF43)  # scroll X pixels
        scy = self.mem.read_byte(0xFF42)  # scroll Y pixels
        vp_tile_x = scx // 8
        vp_tile_y = (scy // 8) if scy < 128 else (scy - 256) // 8
        collision = self.game.game_area_collision()  # shape: [18][20]

        def is_passable(wx: int, wy: int) -> bool:
            col = wx - vp_tile_x
            row = wy - vp_tile_y
            if 0 <= row < 18 and 0 <= col < 20:
                return bool(collision[row][col])
            return True

        return is_passable

    # ------------------------------------------------------------------
    # Single-step movement with confirmation
    # ------------------------------------------------------------------
    def _step(self, direction: str) -> bool:
        dx, dy, press_ev, _ = DIRECTIONS[direction]
        x_before, y_before = self._pos()
        facing_before = self.gs.map["player_facing"]  # add this

        # Add temporarily to _step, before the first press:
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

        self.pyboy.send_input(press_ev)
        for _ in range(self.FRAMES_PER_STEP):
            self.pyboy.tick()
        self.pyboy.send_input(self._release_map[press_ev])
        for _ in range(self.FRAMES_RELEASE + self.WALK_ANIMATION_FRAMES):
            self.pyboy.tick()

        x_after, y_after = self._pos()
        facing_after = self.gs.map["player_facing"]  # add this

        if x_after == x_before + dx and y_after == y_before + dy:
            return True

        if x_after == x_before and y_after == y_before:
            print(f"  [STEP] No move: facing was {facing_before} → {facing_after}, direction={direction}")
            # retry
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
            return moved

        print(f"  [STEP] Wrong move: {direction} from ({x_before},{y_before}) facing={facing_before} → ({x_after},{y_after}) facing={facing_after}")
        return False

    # def _step(self, direction: str) -> bool:
    #     """
    #     Press direction, tick, confirm player moved exactly one tile.
    #     Returns True if position changed as expected.
    #     """
    #     dx, dy, press_ev, _ = DIRECTIONS[direction]
    #     x_before, y_before = self._pos()
        
    #     self.press(press_ev)
    #     # # Extra settle frames before reading position
    #     # for _ in range(10):
    #     #     self.pyboy.tick()

    #     x_after, y_after = self._pos()
    #     moved = x_after == x_before + dx and y_after == y_before + dy
    #     if not moved:
    #         print(f"  [STEP] Failed: {direction} from ({x_before},{y_before}) → stayed, intended ({x_before+dx},{y_before+dy})")
    #         # Print what collision says about that tile RIGHT NOW
    #         passable = self._build_passable_fn()
    #         intended_x, intended_y = x_before + dx, y_before + dy
    #         print(f"  [STEP] Collision says intended tile passable={passable(intended_x, intended_y)}")
    #         print(f"  [STEP] Collision says current tile passable={passable(x_before, y_before)}")
    #         # Print 5x5 around player
    #         for ddy in range(-2, 3):
    #             row = ""
    #             for ddx in range(-2, 3):
    #                 nx, ny = x_before+ddx, y_before+ddy
    #                 mark = "P" if (ddx==0 and ddy==0) else ("." if passable(nx,ny) else "#")
    #                 row += mark + " "
    #             print(f"  [STEP]   {row}")
    #     return x_after == x_before + dx and y_after == y_before + dy

    # ------------------------------------------------------------------
    # Map transition wait
    # ------------------------------------------------------------------

    def _wait_for_map_change(self, expected_map_id: int, timeout: int = 0) -> bool:
        """Tick until map ID becomes expected_map_id, or timeout. Returns True on success."""
        timeout = timeout if timeout != 0 else self.WARP_WAIT_FRAMES
        for _ in range(timeout):
            self.pyboy.tick()
            if self._map_id() == expected_map_id:
                return True
        return False

    # ------------------------------------------------------------------
    # Navigate to a specific tile via A*
    # ------------------------------------------------------------------

    def navigate_to_tile(self, goal_x: int, goal_y: int, max_steps: int = 200) -> bool:
        confirmed_blocked: set[tuple[int, int]] = set()

        for _ in range(max_steps):
            cx, cy = self._pos()
            if cx == goal_x and cy == goal_y:
                return True

            if self._map_id() != self._expected_map_id:
                return False

            passable = self._build_passable_fn()

            def passable_with_memory(wx, wy, _p=passable, _b=confirmed_blocked):
                return (wx, wy) not in _b and _p(wx, wy)

            effective_gx, effective_gy = goal_x, goal_y
            if not passable_with_memory(goal_x, goal_y):
                for ddx, ddy in [(0, -1), (0, 1), (-1, 0), (1, 0)]:
                    nx, ny = goal_x + ddx, goal_y + ddy
                    if passable_with_memory(nx, ny):
                        effective_gx, effective_gy = nx, ny
                        break

            path = astar(cx, cy, effective_gx, effective_gy, passable_with_memory)

            if path is None:
                print(f"  [A*] No path to ({effective_gx},{effective_gy}) from ({cx},{cy})")
                return False

            direction = path[0]
            dx, dy, _, _ = DIRECTIONS[direction]
            intended_x, intended_y = cx + dx, cy + dy

            moved = self._step(direction)

            if not moved:
                print(f"  [A*] Blocked at ({intended_x},{intended_y}), attempting dodge")
                # Dodge perpendicular to intended direction, but only toward goal
                dodged = self._dodge(direction, goal_x, goal_y)
                if not dodged:
                    print(f"  [A*] Dodge failed, marking ({intended_x},{intended_y}) blocked")
                    confirmed_blocked.add((intended_x, intended_y))

            nx, ny = self._pos()
            if (nx, ny) == (effective_gx, effective_gy) and (effective_gx, effective_gy) != (goal_x, goal_y):
                return True

        return self._pos() == (goal_x, goal_y)


    def _dodge(self, blocked_direction: str, goal_x: int, goal_y: int, max_dodge: int = 4) -> bool:
        """
        Try to navigate around a sprite obstacle by stepping perpendicular
        to the blocked direction, biased toward the goal, then retrying.
        
        If blocked going right/left → try up or down (whichever is toward goal)
        If blocked going up/down   → try right or left (whichever is toward goal)
        
        After each perpendicular step, retry the original direction.
        Returns True if we successfully moved past the obstacle.
        """
        cx, cy = self._pos()
        dx_to_goal = goal_x - cx
        dy_to_goal = goal_y - cy

        # Determine which perpendicular directions to try, biased toward goal
        if blocked_direction in ("left", "right"):
            # Blocked horizontally — dodge vertically
            # Primary: whichever vertical direction is toward goal
            # Secondary: the other vertical direction
            if dy_to_goal <= 0:
                perp_dirs = ["up", "down"]
            else:
                perp_dirs = ["down", "up"]
        else:
            # Blocked vertically — dodge horizontally
            if dx_to_goal >= 0:
                perp_dirs = ["right", "left"]
            else:
                perp_dirs = ["left", "right"]

        for perp_dir in perp_dirs:
            for dodge_step in range(1, max_dodge + 1):
                # Try stepping perpendicular
                moved = self._step(perp_dir)
                if not moved:
                    # Can't go this perpendicular direction either, try the other
                    break

                # Now retry the original blocked direction
                retry_moved = self._step(blocked_direction)
                if retry_moved:
                    print(f"  [DODGE] Cleared obstacle after {dodge_step} {perp_dir} step(s)")
                    return True
                # Still blocked — keep dodging in same perpendicular direction

            # This perpendicular direction didn't work, undo and try the other
            # Walk back the dodge steps we took
            undo_dir = OPPOSITE[perp_dir]
            cx_now, cy_now = self._pos()
            cx_orig, cy_orig = cx, cy
            # Figure out how many steps we actually took in perp direction
            if perp_dir in ("up", "down"):
                steps_taken = abs(cy_now - cy_orig)
            else:
                steps_taken = abs(cx_now - cx_orig)
            
            for _ in range(steps_taken):
                self._step(undo_dir)

        return False
    
    def _direct_walk(self, goal_x: int, goal_y: int, max_steps: int = 30) -> bool:
        """
        Fallback for tiny indoor maps: walk directly toward goal
        one step at a time without consulting collision.
        Stops when position matches goal or stops changing (truly stuck).
        """
        print(f"  [DIRECT] Fallback direct walk to ({goal_x},{goal_y})")
        for _ in range(max_steps):
            cx, cy = self._pos()
            if cx == goal_x and cy == goal_y:
                return True
            # Pick axis with larger distance first
            dx = goal_x - cx
            dy = goal_y - cy
            if abs(dx) >= abs(dy):
                direction = "right" if dx > 0 else "left"
            else:
                direction = "down" if dy > 0 else "up"
            prev = self._pos()
            self._step(direction)
            if self._pos() == prev:
                # Truly stuck — try the other axis
                direction = "down" if dy > 0 else "up" if dy < 0 else ("right" if dx > 0 else "left")
                self._step(direction)
        return self._pos() == (goal_x, goal_y)

    # ------------------------------------------------------------------
    # Hop execution
    # ------------------------------------------------------------------

    def _execute_warp_hop(self, warp: dict, dst_map: str) -> bool:
        """Navigate to a warp tile and wait for the map transition to complete."""
        dest_map_id = self.graph.map_id(dst_map)
        self._expected_map_id = self._map_id()
        if dest_map_id is None:
            print(f"  [WARP] {dst_map} has no ID in world graph")
            return False

        print(f"  [WARP] Navigating to warp tile ({warp['x']}, {warp['y']}) → {dst_map}")
        for direction, (dx, dy, _, _) in DIRECTIONS.items():
            approach_tile = warp['x'] - dx, warp['y'] - dy
            self.navigate_to_tile(*approach_tile)
            if self._step(direction):
                if self._wait_for_map_change(dest_map_id) and self._map_id() == dest_map_id:
                    return True
        return self._wait_for_map_change(dest_map_id)

    def _execute_connection_hop(self, arrow_dir: str, dst_map: str) -> bool:
        """
        Walk in arrow_dir until the map ID changes to dst_map.
        For seamless border connections (e.g. Pallet Town → Route 1).
        """
        dst_map_id   = self.graph.map_id(dst_map)
        start_map_id = self._map_id()
        dx, dy, press_ev, _ = DIRECTIONS[arrow_dir]

        for _ in range(100):
            cx, cy = self._pos()
            passable = self._build_passable_fn()

            if passable(cx + dx, cy + dy):
                self.press(press_ev)
            else:
                print(f"  [CONN] Blocked approaching border at ({cx},{cy})")
                return False

            if self._map_id() != start_map_id:
                success = self._map_id() == dst_map_id
                if not success:
                    print(f"  [CONN] Crossed into unexpected map: {self._map_name()}")
                return success

        print(f"  [CONN] Walked 100 steps {arrow_dir} without crossing border into {dst_map}")
        return False

    def _execute_hop(self, src_map: str, dst_map: str) -> bool:
        """Determine edge type (warp vs connection) and execute one map→map hop."""
        # Warp edges take priority
        for warp in self.graph.warps(src_map):
            if warp["dest_map"] == dst_map:
                return self._execute_warp_hop(warp, dst_map)

        # Connection edges
        for compass_dir, conn in self.graph.connections(src_map).items():
            if conn["map"] == dst_map:
                arrow_dir = COMPASS_TO_ARROW[compass_dir]
                print(f"  [CONN] Walking {arrow_dir} to cross into {dst_map}")
                return self._execute_connection_hop(arrow_dir, dst_map)

        print(f"  [ERROR] No graph edge between {src_map} and {dst_map}")
        return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def go_to(self, destination: str) -> bool:
        """
        Navigate to destination map from wherever the player currently is.
        Plans BFS route through world graph, executes each hop in sequence.
        Returns True if destination reached.
        """
        destination = destination.upper()
        current = self._map_name()

        if current is None:
            print(f"[GO_TO] Current map ID {self._map_id():#04x} not in world graph.")
            print(f"        game_state reports: {self.gs.map['map_name']}")
            print(f"        Check that map_constants.asm was parsed correctly.")
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