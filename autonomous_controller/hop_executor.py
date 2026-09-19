"""Door and seamless-connection execution using reachable terrain paths."""

from autonomous_controller.constants import COMPASS_TO_ARROW, DIRECTIONS


class HopExecutor:
    def _warp_tiles(self):
        return {(x, y) for x, y, _, _ in self._current_warps_from_ram()}

    def _execute_warp_hop(self, warp, dst_map):
        origin, target = self._map_id(), self.graph.map_id(dst_map)
        wx, wy = warp["x"], warp["y"]
        forbidden = self._warp_tiles()
        candidates = []
        for direction, (dx, dy, _, _) in DIRECTIONS.items():
            approach = (wx - dx, wy - dy)
            if not self.rom_pass.is_passable(self._map_name(), *approach):
                continue
            path = self._plan_path(approach, forbidden)
            if path is not None:
                candidates.append((len(path), direction, approach))
        for _, direction, approach in sorted(candidates):
            if self.navigate_to_tile(*approach, forbidden_tiles=forbidden):
                self._step(direction)
                # Indoor exits trigger when walking out of the doorway square,
                # whereas stairs normally trigger when stepping onto it.
                if (
                    self._map_id() == origin
                    and self._pos() == (wx, wy)
                    and not self.interrupt.was_displaced
                ):
                    name = self._map_name()
                    outward = (
                        "down"
                        if wy == self.rom_pass.map_height(name) - 1
                        else "up"
                        if wy == 0
                        else "right"
                        if wx == self.rom_pass.map_width(name) - 1
                        else "left"
                        if wx == 0
                        else direction
                    )
                    self._step(outward)
                if self._map_id() == origin:
                    self._wait_for_map_change(target, 30)
            if self._map_id() != origin:
                return self._map_id() == target
            if self.interrupt.was_displaced:
                return False
        return False

    def _execute_connection_hop(self, arrow_dir, dst_map):
        src, origin = self._map_name(), self._map_id()
        target = self.graph.map_id(dst_map)
        compass = next(k for k, v in COMPASS_TO_ARROW.items() if v == arrow_dir)
        offset = self.graph.connections(src)[compass].get("offset", 0)
        forbidden, candidates = self._warp_tiles(), []
        for border, _ in self.rom_pass.connection_tiles(src, dst_map, arrow_dir, offset):
            path = self._plan_path(border, forbidden)
            if path is not None:
                candidates.append((len(path), border))
        for _, border in sorted(candidates):
            print(f"  [CONN] {src} -> {dst_map}: crossing {border} {arrow_dir}")
            if self.navigate_to_tile(*border, forbidden_tiles=forbidden):
                self._step(arrow_dir)
                if self._map_id() == origin:
                    self._wait_for_map_change(target, 30)
            if self._map_id() != origin:
                return self._map_id() == target
            if self.interrupt.was_displaced:
                return False
        return False

    def _execute_hop(self, src_map, dst_map):
        # Try every reachable entrance, not only the first warp in file order.
        for warp in self.graph.warps(src_map):
            if warp["dest_map"] == dst_map:
                if self._execute_warp_hop(warp, dst_map):
                    return True
                if self._map_name() != src_map or self.interrupt.was_displaced:
                    return False
        for compass, conn in self.graph.connections(src_map).items():
            if conn["map"] == dst_map:
                return self._execute_connection_hop(COMPASS_TO_ARROW[compass], dst_map)
        return False
