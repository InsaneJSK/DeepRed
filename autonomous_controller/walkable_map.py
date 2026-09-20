"""Full-map terrain. Public coordinates are 16px player steps, not 32px blocks."""

from functools import lru_cache

from autonomous_controller.game_data import load_bundle


class RomPassability:
    def __init__(self, bundle_path=None):
        data = load_bundle(bundle_path)["terrain"]
        self.dims = {name: tuple(size) for name, size in data["dimensions"].items()}
        self.headers = {name: tuple(header) for name, header in data["headers"].items()}
        self.collision = {name: set(tiles) for name, tiles in data["collision"].items()}
        self.pairs = {
            name: {frozenset(pair) for pair in pairs} for name, pairs in data["pairs"].items()
        }
        self._tiles = {
            name: tuple(tuple(row) for row in grid) for name, grid in data["tiles"].items()
        }

    def map_width(self, name):
        return self.dims.get(name, (0, 0))[0] * 2

    def map_height(self, name):
        return self.dims.get(name, (0, 0))[1] * 2

    def tiles(self, name):
        return self._tiles.get(name, ())

    def tile(self, name, x, y):
        grid = self.tiles(name)
        return grid[y][x] if grid and 0 <= y < len(grid) and 0 <= x < len(grid[0]) else None

    def is_passable(self, name, x, y):
        tile = self.tile(name, x, y)
        tileset = self.headers.get(name, ("", ""))[1]
        return tile is not None and tile in self.collision.get(tileset, set())

    def can_step(self, name, x, y, nx, ny):
        if abs(nx - x) + abs(ny - y) != 1 or not self.is_passable(name, nx, ny):
            return False
        tileset = self.headers.get(name, ("", ""))[1]
        return frozenset((self.tile(name, x, y), self.tile(name, nx, ny))) not in self.pairs.get(
            tileset, set()
        )

    def connection_tiles(self, src, dst, direction, offset=0):
        """Touching source/destination squares, accounting for connection offsets."""
        w, h = self.map_width(src), self.map_height(src)
        dw, dh = self.map_width(dst), self.map_height(dst)
        for value in range(w if direction in ("up", "down") else h):
            if direction == "up":
                a, b = (value, 0), (value - 2 * offset, dh - 1)
            elif direction == "down":
                a, b = (value, h - 1), (value - 2 * offset, 0)
            elif direction == "left":
                a, b = (0, value), (dw - 1, value - 2 * offset)
            else:
                a, b = (w - 1, value), (0, value - 2 * offset)
            if self.is_passable(src, *a) and self.is_passable(dst, *b):
                yield a, b

    @lru_cache(maxsize=256)
    def components(self, name):
        """Connected walking regions; one map ID can contain several islands."""
        regions = {}
        component = 0
        for y in range(self.map_height(name)):
            for x in range(self.map_width(name)):
                if (x, y) in regions or not self.is_passable(name, x, y):
                    continue
                regions[x, y] = component
                todo = [(x, y)]
                while todo:
                    cx, cy = todo.pop()
                    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                        nxt = (cx + dx, cy + dy)
                        if nxt not in regions and self.can_step(name, cx, cy, *nxt):
                            regions[nxt] = component
                            todo.append(nxt)
                component += 1
        return regions
