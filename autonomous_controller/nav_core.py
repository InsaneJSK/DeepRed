"""Bounded button input and live-state primitives for navigation."""

from autonomous_controller.constants import ADDR_MAP_ID, ADDR_WARP_BASE, ADDR_WARP_COUNT, DIRECTIONS


class NavCore:
    FRAMES_PER_STEP = 2
    FRAMES_RELEASE = 1
    WALK_ANIMATION_FRAMES = 17
    WARP_WAIT_FRAMES = 120

    def press(self, button, frames=0):
        self.pyboy.send_input(button)
        try:
            self.pyboy.tick(frames or self.FRAMES_PER_STEP)
        finally:
            self.pyboy.send_input(self._release_map[button])
        self.pyboy.tick(self.FRAMES_RELEASE)

    def _pos(self):
        return self.gs.map["player_x"], self.gs.map["player_y"]

    def _map_id(self):
        return self.gs.mem.read_byte(ADDR_MAP_ID)

    def _map_name(self):
        return self.graph.map_name(self._map_id())

    def _current_warps_from_ram(self):
        result = []
        for i in range(min(32, self.gs.mem.read_byte(ADDR_WARP_COUNT))):
            base = ADDR_WARP_BASE + i * 4
            y, x, index, dest = self.gs.mem.read_bytes(base, 4)
            result.append((x, y, dest, index))
        return result

    def _occupied_tiles(self):
        result = set()
        for obj in self.gs.map_objects:
            if not obj["visible"]:
                continue
            x, y = obj["x"], obj["y"]
            result.add((x, y))
            # Map coordinates already point to the destination during a walk.
            # Reserve the square being vacated as well, until animation ends.
            if obj["moving"]:
                result.add((x - obj["dx"], y - obj["dy"]))
        result.discard(self._pos())
        return result

    def _build_passable_fn(self):
        name, occupied = self._map_name(), self._occupied_tiles()
        return lambda x, y: (x, y) not in occupied and self.rom_pass.is_passable(name, x, y)

    def _wait_for_map_change(self, expected_map_id, timeout=0):
        for _ in range(timeout or self.WARP_WAIT_FRAMES):
            if self._map_id() == expected_map_id:
                return True
            self.pyboy.tick()
        return self._map_id() == expected_map_id

    def _step(self, direction):
        """One walking step, with a facing-only retry and map-change detection."""
        dx, dy, press, release = DIRECTIONS[direction]
        origin, start = self._map_id(), self._pos()
        self.interrupt.was_displaced = False
        self.nav_stats["step_calls"] += 1
        for _ in range(2):
            self.pyboy.send_input(press)
            try:
                self.pyboy.tick(self.FRAMES_PER_STEP)
            finally:
                self.pyboy.send_input(release)
            self.pyboy.tick(self.FRAMES_RELEASE + self.WALK_ANIMATION_FRAMES)
            if self._map_id() != origin:
                self.pyboy.tick(30)
                self.interrupt.check_and_handle()
                return True
            if self._pos() != start:
                expected = (start[0] + dx, start[1] + dy)
                self.interrupt.check_and_handle()
                return self._pos() == expected and not self.interrupt.was_displaced
            self.interrupt.check_and_handle()
            if self._map_id() != origin or self.interrupt.was_displaced:
                return False
        self.nav_stats["blocked_steps"] += 1
        return False
