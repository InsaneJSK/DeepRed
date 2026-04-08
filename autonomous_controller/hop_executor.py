"""
autonomous_controller/hop_executor.py

HopExecutor mixin — map-to-map hop execution (warp doors + map connections).

Expects NavCore + NavAstar in the MRO:
    self.graph         — WorldGraph
    self.rom_pass      — RomPassability
    self.gs            — PokemonGameState (for RAM reads)
    self._expected_map_id  — int
    navigate_to_tile() — from NavAstar
    _step(), press()   — from NavCore
    _pos(), _map_id(), _map_name(), _current_warps_from_ram(),
    _wait_for_map_change() — from NavCore
"""

from typing import Any, Protocol

from autonomous_controller.constants import DIRECTIONS, COMPASS_TO_ARROW
from autonomous_controller.walkable_map import RomPassability
from autonomous_controller.world_graph import WorldGraph
from memory_state.game_state import PokemonGameState

class _HopExectorDeps(Protocol):
    graph: WorldGraph
    gs: PokemonGameState
    rom_pass: RomPassability
    _expected_map_id: int

    def navigate_to_tile( #pylint: ignore: disable=missing-function-docstring
            self,
            x: int,
            y: int,
            forbidden_tiles: set[tuple[int, int]] | None = None) -> bool: ...
    def _step(self, direction: str) -> bool: ...
    def press(self, event: Any) -> None: ... #pylint: ignore: disable=missing-function-docstring
    def _pos(self) -> tuple[int, int]: ...
    def _map_id(self) -> int: ...
    def _map_name(self) -> str | None: ...
    def _current_warps_from_ram(self) -> list[tuple[int, int, int, int]]: ...
    def _wait_for_map_change(self, expected_map_id: int, timeout_ms: int = 5000) -> bool: ...

class HopExecutor(_HopExectorDeps):
    """Mixin: warp-hop, connection-hop, and single-edge hop dispatch."""

    # Warp hop

    def _execute_warp_hop(self, warp: dict, dst_map: str) -> bool:
        """
        Navigate to a warp tile and step onto it in the correct direction.

        Tries all four approach directions in order (up/down/left/right).
        Only attempts the warp step if navigate_to_tile actually reached the
        approach tile — wrong-position steps would land on the wrong tile
        or trigger nothing at all.
        """
        dest_map_id = self.graph.map_id(dst_map)
        self._expected_map_id = self._map_id() #pylint: ignore: disable=assignment-from-no-return
        if dest_map_id is None:
            print(f"  [WARP] {dst_map} has no ID in world graph")
            return False

        warp_x, warp_y = warp["x"], warp["y"]
        print(f"  [WARP] Navigating to warp tile ({warp_x}, {warp_y}) → {dst_map}")

        for direction, (dx, dy, _, _) in DIRECTIONS.items():
            approach_x = warp_x - dx
            approach_y = warp_y - dy
            self.navigate_to_tile(approach_x, approach_y)
            cx, cy = self._pos() #pylint: disable=assignment-from-no-return, disable=unpacking-non-sequence
            if cx != approach_x or cy != approach_y:
                print(f"  [WARP] Couldn't reach approach ({approach_x},{approach_y}) "
                      f"for {direction}, at ({cx},{cy})")
                continue
            if self._step(direction):
                if self._wait_for_map_change(dest_map_id) and self._map_id() == dest_map_id:
                    return True

        return self._wait_for_map_change(dest_map_id)

    # Connection-border helpers

    def _scan_border_avoiding_warps( #pylint: disable=too-many-arguments, disable=too-many-locals, too-many-positional-arguments
        self,
        arrow_dir: str,
        cx: int,
        cy: int,
        src_map: str,
        map_h: int,
        map_w: int,
        warp_tiles: set[tuple[int, int]],
        exclude: set[tuple[int, int]] | None = None,
    ) -> tuple[int, int] | None:
        """
        Scan the connection border strip (ROM-based) and return the nearest
        player-step coordinate that is:
          - ROM-passable (not a wall block)
          - NOT a warp tile (won't teleport the player into a building)
          - NOT in the exclude set (used by fallback to avoid re-trying same goal)
        """
        if exclude is None:
            exclude = set()

        if arrow_dir in ("up", "down"):
            border_y = 0 if arrow_dir == "up" else map_h - 1
            strip_block = border_y // 2
            block_width = self.rom_pass.map_width(src_map) or (map_w // 2)
            for bx in sorted(range(block_width), key=lambda b: abs(b - cx // 2)):
                px = bx * 2
                if (px, border_y) in warp_tiles or (px, border_y) in exclude:
                    continue
                if self.rom_pass.is_passable(src_map, bx=bx, by=strip_block):
                    return px, border_y
        else:  # left / right
            border_x = 0 if arrow_dir == "left" else map_w - 1
            strip_block = border_x // 2
            block_height = self.rom_pass.map_height(src_map) or (map_h // 2)
            for by in sorted(range(block_height), key=lambda b: abs(b - cy // 2)):
                py = by * 2
                if (border_x, py) in warp_tiles or (border_x, py) in exclude:
                    continue
                if self.rom_pass.is_passable(src_map, bx=strip_block, by=by):
                    return border_x, py
        return None

    # Connection hop

    def _execute_connection_hop(self, arrow_dir: str, dst_map: str) -> bool: #pylint: disable=too-many-locals, disable=too-many-branches, disable=too-many-statements
        """
        Walk from the current map to an adjacent map via a map connection
        (open border, not a warp door).

        Coordinate notes
        ----------------
        Player x/y (from RAM) are in player-step units: 1 step = 16 px = 2 tiles.
        ROM .blk files store one byte per block = 32 px = 2 player steps.
            block_coord  = player_coord // 2
            player_coord = block_coord  * 2
        map_h / map_w (from 0xD368/0xD369) are in player-step units.
        """
        dst_map_id   = self.graph.map_id(dst_map)
        start_map_id = self._map_id() #pylint: disable=assignment-from-no-return
        _, _, press_ev, _ = DIRECTIONS[arrow_dir]
        cx, cy = self._pos() #pylint: disable=assignment-from-no-return, disable=unpacking-non-sequence
        src_map = self._map_name() #pylint: disable=assignment-from-no-return

        map_h = self.gs.mem.read_byte(0xD368)   # player-step units
        map_w = self.gs.mem.read_byte(0xD369)   # player-step units

        # Collect live warp positions so A* avoids them (stepping on a warp
        # during connection navigation teleports the player into a building).
        warp_tiles: set[tuple[int, int]] = set()
        for wx, wy, _, _ in self._current_warps_from_ram(): #pylint: disable=not-an-iterable
            warp_tiles.add((wx, wy))

        # Pick the nearest walkable tile on the border strip via ROM lookup
        if arrow_dir == "up":
            border_y = 0
            best_block = self.rom_pass.nearest_walkable_on_strip(
                src_map, scan_coord=cx // 2, axis="x", strip_index=border_y // 2
            ) if src_map else None
            if best_block is None:
                border_x = cx
                print(f"  [CONN] ROM unavailable for {src_map}, using cx={cx}")
            else:
                border_x = best_block * 2
                print(f"  [CONN] ROM top strip: block {best_block} → player x={border_x}")

        elif arrow_dir == "down":
            border_y = map_h - 1
            best_block = self.rom_pass.nearest_walkable_on_strip(
                src_map, scan_coord=cx // 2, axis="x", strip_index=border_y // 2
            ) if src_map else None
            if best_block is None:
                border_x = cx
                print(f"  [CONN] ROM unavailable for {src_map}, using cx={cx}")
            else:
                border_x = best_block * 2
                print(f"  [CONN] ROM bottom strip: block {best_block} → player x={border_x}")

        elif arrow_dir == "left":
            border_x = 0
            best_block = self.rom_pass.nearest_walkable_on_strip(
                src_map, scan_coord=cy // 2, axis="y", strip_index=border_x // 2
            ) if src_map else None
            if best_block is None:
                border_y = cy
                print(f"  [CONN] ROM unavailable for {src_map}, using cy={cy}")
            else:
                border_y = best_block * 2
                print(f"  [CONN] ROM left strip: block {best_block} → player y={border_y}")

        else:  # right
            border_x = map_w - 1
            best_block = self.rom_pass.nearest_walkable_on_strip(
                src_map, scan_coord=cy // 2, axis="y", strip_index=border_x // 2
            ) if src_map else None
            if best_block is None:
                border_y = cy
                print(f"  [CONN] ROM unavailable for {src_map}, using cy={cy}")
            else:
                border_y = best_block * 2
                print(f"  [CONN] ROM right strip: block {best_block} → player y={border_y}")

        # Make sure the chosen border tile isn't itself forbidden
        warp_tiles.discard((border_x, border_y))

        print(f"  [CONN] Navigating to border tile ({border_x},{border_y}), "
              f"avoiding {len(warp_tiles)} warp(s)")
        self._expected_map_id = start_map_id
        nav_ok = self.navigate_to_tile(border_x, border_y, forbidden_tiles=warp_tiles) #pylint: disable=assignment-from-no-return

        # Check if we already crossed (map transition fired mid-walk)
        if self._map_id() == dst_map_id:
            return True

        # Fallback: re-scan from the actual failure position
        if not nav_ok and self._map_id() == start_map_id:
            cx2, cy2 = self._pos() #pylint: disable=assignment-from-no-return, disable=unpacking-non-sequence
            src_map2 = self._map_name() #pylint: disable=assignment-from-no-return
            print(f"  [CONN] Nav failed at ({cx2},{cy2}); re-scanning strip from failure point")
            fb = self._scan_border_avoiding_warps(
                arrow_dir, cx2, cy2, src_map2 or src_map, map_h, map_w, warp_tiles,  # type: ignore
                exclude={(border_x, border_y)},  # never re-try the same tile
            )
            if fb is not None:
                fb_x, fb_y = fb
                warp_tiles.discard((fb_x, fb_y))
                print(f"  [CONN] Fallback border tile: ({fb_x},{fb_y})")
                self.navigate_to_tile(fb_x, fb_y, forbidden_tiles=warp_tiles)
                if self._map_id() == dst_map_id:
                    return True
            else:
                print("[CONN] Fallback strip scan find no reachable tile (tried all non-warps)")

        # Walk off the edge — keep pressing the direction until map changes
        for _ in range(10):
            self.press(press_ev)
            if self._map_id() != start_map_id:
                return self._map_id() == dst_map_id

        print(f"  [CONN] Failed to cross border into {dst_map}")
        return False

    # Hop dispatch

    def _execute_hop(self, src_map: str, dst_map: str) -> bool:
        """Determine edge type (warp vs connection) and execute one map→map hop."""
        for warp in self.graph.warps(src_map):
            if warp["dest_map"] == dst_map:
                return self._execute_warp_hop(warp, dst_map)

        for compass_dir, conn in self.graph.connections(src_map).items():
            if conn["map"] == dst_map:
                arrow_dir = COMPASS_TO_ARROW[compass_dir]
                print(f"  [CONN] Walking {arrow_dir} to cross into {dst_map}")
                return self._execute_connection_hop(arrow_dir, dst_map)

        print(f"  [ERROR] No graph edge between {src_map} and {dst_map}")
        return False
