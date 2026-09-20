"""Full-map terrain. Public coordinates are 16px player steps, not 32px blocks."""

import re
from functools import lru_cache
from pathlib import Path

from autonomous_controller.walkable_map import RomPassability


def norm(value):
    return value.upper().replace("_", "")


class SourceTerrain(RomPassability):
    def __init__(self, pokered_root):
        self.root = Path(pokered_root)
        constants = (self.root / "constants/map_constants.asm").read_text()
        self.dims = {
            name: (int(w), int(h))
            for name, w, h in re.findall(r"map_const\s+(\w+),\s*(\d+),\s*(\d+)", constants)
        }
        self.headers = {}
        for path in (self.root / "data/maps/headers").glob("*.asm"):
            match = re.search(r"map_header\s+(\w+),\s*(\w+),\s*(\w+)", path.read_text())
            if match:
                stem, name, tileset = match.groups()
                self.headers[name] = (stem, norm(tileset))
        self.blocks = self._binary_labels(self.root / "maps.asm", "_Blocks")
        self.blocksets = {
            norm(k): v
            for k, v in self._binary_labels(self.root / "gfx/tilesets.asm", "_Block").items()
        }
        self.collision, pending = {}, []
        for line in (self.root / "data/tilesets/collision_tile_ids.asm").read_text().splitlines():
            line = line.split(";")[0].strip()
            label = re.match(r"(\w+)_Coll::", line)
            if label:
                pending.append(norm(label[1]))
            elif line.startswith("coll_tiles"):
                values = {int(v, 16) for v in re.findall(r"\$([\da-fA-F]+)", line)}
                for label in pending:
                    self.collision[label] = values
                pending.clear()
        pairs = (self.root / "data/tilesets/pair_collision_tile_ids.asm").read_text()
        self.pairs = {}
        for tileset, a, b in re.findall(
            r"db\s+(\w+),\s*\$(\w+),\s*\$(\w+)", pairs.split("TilePairCollisionsWater")[0]
        ):
            self.pairs.setdefault(norm(tileset), set()).add(frozenset((int(a, 16), int(b, 16))))

    def _binary_labels(self, path, suffix):
        result, pending = {}, []
        for raw in path.read_text().splitlines():
            line = raw.split(";")[0]
            label = re.search(r"(\w+)" + suffix + r"::?", line)
            if label:
                pending.append(label[1])
            binary = re.search(r'INCBIN\s+"([^"]+)"', line)
            if binary:
                for name in pending:
                    result[name] = self.root / binary[1]
                pending.clear()
        return result

    def map_width(self, name):
        return self.dims.get(name, (0, 0))[0] * 2

    def map_height(self, name):
        return self.dims.get(name, (0, 0))[1] * 2

    @lru_cache(maxsize=256)
    def tiles(self, name):
        """Lower-left 8px tile of each 16px square; missing data fails closed."""
        if name not in self.headers or name not in self.dims:
            return ()
        stem, tileset = self.headers[name]
        map_file, set_file = self.blocks.get(stem), self.blocksets.get(tileset)
        if not map_file or not set_file:
            return ()
        blocks, definitions = map_file.read_bytes(), set_file.read_bytes()
        w, h = self.dims[name]
        if len(blocks) != w * h:
            raise ValueError(f"{name}: block layout does not match dimensions")
        return tuple(
            tuple(
                definitions[blocks[(y // 2) * w + x // 2] * 16 + (y % 2) * 8 + (x % 2) * 2 + 4]
                for x in range(w * 2)
            )
            for y in range(h * 2)
        )
