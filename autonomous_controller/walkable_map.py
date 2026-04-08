"""
autonomous_controller/walkable_map.py

ROM-based map passability reader for Pokemon Red.

Reads the pret/pokered disassembly files to determine which tiles on a map
are actually walkable — specifically useful for border-strip navigation
during connection hops, where the viewport-based collision check is unreliable.

Key concepts
------------
* Map block layout  – stored in maps/<MapName>.blk as a flat grid of block IDs,
  row-major, width × height (in blocks).  Each block ID corresponds to one
  player-coordinate tile, since the player moves in block-aligned steps.
* Tileset collision lists – defined in data/tilesets/collision_tile_ids.asm.
  Each entry is a list of block IDs that are *impassable* for that tileset.
  A block ID NOT in the collision list is passable.
* Map↔tileset mapping – parsed from data/maps/headers/*.asm.
  The first positional arg after the map name is the map constant (ignored here),
  and the second positional arg is the tileset name.
* Map dimensions – parsed from constants/map_constants.asm
  ``map_const MAP_NAME, WIDTH, HEIGHT``.

Usage
-----
    from autonomous_controller.walkable_map import RomPassability

    rom_pass = RomPassability(pokered_root="path/to/pokered")
    # Check if the tile at block (x=3, y=0) on ROUTE_1 is walkable:
    ok = rom_pass.is_passable("ROUTE_1", bx=3, by=0)

    # Find nearest walkable x on the top border (y=0) closest to player_x:
    best_x = rom_pass.nearest_walkable_border_x("ROUTE_1", player_x=5, border_y=0)
"""

import re
from pathlib import Path
from functools import lru_cache


# ---------------------------------------------------------------------------
# Tileset index (order matches tileset_headers.asm / map_header tileset arg)
# ---------------------------------------------------------------------------

# Ordered list of tileset names exactly as they appear in tileset_headers.asm.
# The name used in map_header is the UPPERCASE version (e.g. OVERWORLD, FOREST).
_TILESET_NAMES = [
    "OVERWORLD",
    "REDSHOUSE1",
    "MART",
    "FOREST",
    "REDSHOUSE2",
    "DOJO",
    "POKECENTER",
    "GYM",
    "HOUSE",
    "FORESTGATE",
    "MUSEUM",
    "UNDERGROUND",
    "GATE",
    "SHIP",
    "SHIPPORT",
    "CEMETERY",
    "INTERIOR",
    "CAVERN",
    "LOBBY",
    "MANSION",
    "LAB",
    "CLUB",
    "FACILITY",
    "PLATEAU",
]


def _norm_tileset(name: str) -> str:
    """Normalise a tileset name to lookup key form (no underscores, uppercase)."""
    return name.upper().replace("_", "")


# ---------------------------------------------------------------------------
# Parse collision tile IDs from collision_tile_ids.asm
# ---------------------------------------------------------------------------

def _parse_collision_tile_ids(pokered_root: Path) -> dict[str, set[int]]:
    """
    Returns {tileset_key: set_of_impassable_block_ids}.

    Parses patterns like:
        Forest_Coll::
            coll_tiles $1E, $20, ...
    Multiple labels can share one coll_tiles line (e.g. RedsHouse1_Coll:: / RedsHouse2_Coll::).
    """
    coll_file = pokered_root / "data" / "tilesets" / "collision_tile_ids.asm"
    if not coll_file.exists():
        raise FileNotFoundError(f"Cannot find {coll_file}")

    label_re   = re.compile(r'^([A-Za-z0-9_]+)_Coll::')
    tiles_re   = re.compile(r'coll_tiles(.*)')
    hex_re     = re.compile(r'\$([0-9A-Fa-f]{2})')

    result: dict[str, set[int]] = {}
    pending_labels: list[str] = []

    with open(coll_file, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.split(";")[0].rstrip()

            m_label = label_re.match(line.strip())
            if m_label:
                pending_labels.append(_norm_tileset(m_label.group(1)))
                continue

            m_tiles = tiles_re.search(line)
            if m_tiles and pending_labels:
                hex_values = {int(h, 16) for h in hex_re.findall(m_tiles.group(1))}
                for label in pending_labels:
                    result[label] = hex_values
                pending_labels = []

    return result


# ---------------------------------------------------------------------------
# Parse map dimensions from map_constants.asm
# ---------------------------------------------------------------------------

def _parse_map_dimensions(pokered_root: Path) -> dict[str, tuple[int, int]]:
    """
    Returns {MAP_NAME: (width_blocks, height_blocks)}.
    Parses ``map_const MAP_NAME, W, H`` lines.
    """
    const_file = pokered_root / "constants" / "map_constants.asm"
    if not const_file.exists():
        raise FileNotFoundError(f"Cannot find {const_file}")

    pattern = re.compile(
        r'^\s*map_const\s+([A-Z0-9_]+)\s*,\s*(\d+)\s*,\s*(\d+)',
        re.IGNORECASE,
    )
    dims: dict[str, tuple[int, int]] = {}
    with open(const_file, encoding="utf-8") as f:
        for line in f:
            line = line.split(";")[0]
            m = pattern.match(line)
            if m:
                dims[m.group(1).upper()] = (int(m.group(2)), int(m.group(3)))
    return dims


# ---------------------------------------------------------------------------
# Parse map tileset from data/maps/headers/*.asm
# ---------------------------------------------------------------------------

def _parse_map_tilesets(pokered_root: Path) -> dict[str, str]:
    """
    Returns {MAP_NAME: tileset_key} by parsing map header files.

    The map_header macro has the form:
        map_header <CamelName>, MAP_CONST, TILESET_NAME, FLAGS
    We extract the third positional argument (TILESET_NAME).
    """
    headers_dir = pokered_root / "data" / "maps" / "headers"
    if not headers_dir.exists():
        raise FileNotFoundError(f"Cannot find {headers_dir}")

    # map_header PalletTown, PALLET_TOWN, OVERWORLD, NORTH | SOUTH
    header_re = re.compile(
        r'^\s*map_header\s+\w+\s*,\s*([A-Z0-9_]+)\s*,\s*([A-Z0-9_]+)',
        re.IGNORECASE,
    )

    tilesets: dict[str, str] = {}
    for asm_file in sorted(headers_dir.glob("*.asm")):
        with open(asm_file, encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.split(";")[0]
                m = header_re.match(line)
                if m:
                    map_const   = m.group(1).upper()
                    tileset_raw = m.group(2).upper()
                    tilesets[map_const] = _norm_tileset(tileset_raw)
    return tilesets


# ---------------------------------------------------------------------------
# Map name → .blk filename heuristic
# ---------------------------------------------------------------------------

# Manual overrides for maps whose .blk name doesn't match a simple camel-case
# conversion of the map constant.  Key = MAP_CONSTANT, value = blk stem.
_BLK_OVERRIDES: dict[str, str | None] = {
    "UNDERGROUND_PATH_NORTH_SOUTH": "UndergroundPathNorthSouth",
    "UNDERGROUND_PATH_WEST_EAST":   "UndergroundPathWestEast",
    "UNDERGROUND_PATH_ROUTE_5":     "UndergroundPathRoute5",
    "UNDERGROUND_PATH_ROUTE_6":     "UndergroundPathRoute6",
    "UNDERGROUND_PATH_ROUTE_6_COPY":"UndergroundPathRoute6",
    "UNDERGROUND_PATH_ROUTE_7":     "UndergroundPathRoute7",
    "UNDERGROUND_PATH_ROUTE_7_COPY":"UndergroundPathRoute7",
    "UNDERGROUND_PATH_ROUTE_8":     "UndergroundPathRoute8",
    "SS_ANNE_1F":                   "SSAnne1F",
    "SS_ANNE_2F":                   "SSAnne2F",
    "SS_ANNE_3F":                   "SSAnne3F",
    "SS_ANNE_B1F":                  "SSAnneB1F",
    "SS_ANNE_BOW":                  "SSAnneBow",
    "SS_ANNE_KITCHEN":              "SSAnneKitchen",
    "SS_ANNE_CAPTAINS_ROOM":        "SSAnneCaptainsRoom",
    "SS_ANNE_1F_ROOMS":             "SSAnne1FRooms",
    "SS_ANNE_2F_ROOMS":             "SSAnne2FRooms",
    "SS_ANNE_B1F_ROOMS":            "SSAnneB1FRooms",
    "CERULEAN_TRASHED_HOUSE_COPY":  "CeruleanTrashedHouse",
    "CINNABAR_MART_COPY":           "CinnabarMart",
    "UNUSED_MAP_0B":                None,
    "UNUSED_MAP_69":                None,
    "UNUSED_MAP_6A":                None,
    "UNUSED_MAP_6B":                None,
    "UNUSED_MAP_6D":                None,
    "UNUSED_MAP_6E":                None,
    "UNUSED_MAP_6F":                None,
    "UNUSED_MAP_70":                None,
    "UNUSED_MAP_72":                None,
    "UNUSED_MAP_73":                None,
    "UNUSED_MAP_74":                None,
    "UNUSED_MAP_75":                None,
    "UNUSED_MAP_CC":                None,
    "UNUSED_MAP_CD":                None,
    "UNUSED_MAP_CE":                None,
    "UNUSED_MAP_E7":                None,
    "UNUSED_MAP_ED":                None,
    "UNUSED_MAP_EE":                None,
    "UNUSED_MAP_F1":                None,
    "UNUSED_MAP_F2":                None,
    "UNUSED_MAP_F3":                None,
    "UNUSED_MAP_F4":                None,
}


def _const_to_camel(const: str) -> str:
    """Convert SNAKE_CASE constant to CamelCase blk filename stem."""
    return "".join(word.capitalize() for word in const.split("_"))


def _find_blk(maps_dir: Path, map_name: str) -> Path | None:
    """Locate the .blk file for map_name. Returns None if not found."""
    if map_name in _BLK_OVERRIDES:
        stem = _BLK_OVERRIDES[map_name]
        if stem is None:
            return None
        return maps_dir / f"{stem}.blk"
    camel = _const_to_camel(map_name)
    candidate = maps_dir / f"{camel}.blk"
    return candidate if candidate.exists() else None


# ---------------------------------------------------------------------------
# RomPassability
# ---------------------------------------------------------------------------

class RomPassability:
    """
    Reads map blockdata and tileset collision lists from the pokered
    disassembly to determine which tiles are walkable without needing
    a live PyBoy viewport.

    Parameters
    ----------
    pokered_root : str | Path
        Root directory of the cloned pret/pokered repository.
    """

    def __init__(self, pokered_root: str | Path):
        self._root      = Path(pokered_root).resolve()
        self._maps_dir  = self._root / "maps"

        # Lazy-parsed tables (populated on first use)
        self._coll_ids: dict[str, set[int]] | None = None    # tileset→blocids
        self._dims:     dict[str, tuple[int, int]] | None = None  # map→(w,h)
        self._tilesets: dict[str, str] | None = None         # map→tileset key

    # ------------------------------------------------------------------
    # Lazy loaders
    # ------------------------------------------------------------------

    def _get_coll_ids(self) -> dict[str, set[int]]:
        if self._coll_ids is None:
            self._coll_ids = _parse_collision_tile_ids(self._root)
        return self._coll_ids

    def _get_dims(self) -> dict[str, tuple[int, int]]:
        if self._dims is None:
            self._dims = _parse_map_dimensions(self._root)
        return self._dims

    def _get_tilesets(self) -> dict[str, str]:
        if self._tilesets is None:
            self._tilesets = _parse_map_tilesets(self._root)
        return self._tilesets

    # ------------------------------------------------------------------
    # Block data
    # ------------------------------------------------------------------

    @lru_cache(maxsize=64)
    def _load_blocks(self, map_name: str) -> bytes | None:
        """
        Load the raw .blk bytes for map_name.
        Returns None if the map has no .blk file (unused/virtual maps).
        """
        blk_path = _find_blk(self._maps_dir, map_name)
        if blk_path is None or not blk_path.exists():
            return None
        return blk_path.read_bytes()

    def _block_at(self, map_name: str, bx: int, by: int) -> int | None:
        """
        Return the block ID at position (bx, by) in map_name.
        Returns None if the position is out of bounds or the map has no data.
        """
        dims = self._get_dims().get(map_name)
        if dims is None:
            return None
        w, h = dims
        if not (0 <= bx < w and 0 <= by < h):
            return None
        blocks = self._load_blocks(map_name)
        if blocks is None:
            return None
        idx = by * w + bx
        if idx >= len(blocks):
            return None
        return blocks[idx]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_passable(self, map_name: str, bx: int, by: int) -> bool:
        """
        Return True if block (bx, by) in map_name is walkable per ROM data.

        Falls back to True (optimistic / assume passable) when:
        - The map has no .blk file
        - The tileset is unknown
        - The position is out of bounds
        """
        block_id = self._block_at(map_name, bx, by)
        if block_id is None:
            return True   # no ROM data → assume passable

        tileset_key = self._get_tilesets().get(map_name)
        if tileset_key is None:
            return True   # unknown tileset → assume passable

        coll_set = self._get_coll_ids().get(tileset_key)
        if coll_set is None:
            return True   # unknown collision list → assume passable

        return block_id not in coll_set

    def map_width(self, map_name: str) -> int | None:
        """Return map width in blocks, or None if unknown."""
        dims = self._get_dims().get(map_name)
        return dims[0] if dims else None

    def map_height(self, map_name: str) -> int | None:
        """Return map height in blocks, or None if unknown."""
        dims = self._get_dims().get(map_name)
        return dims[1] if dims else None

    def nearest_walkable_on_strip(
        self,
        map_name: str,
        scan_coord: int,
        axis: str,          # "x" (scanning along x, fixed y) or "y" (scanning along y, fixed x)
        strip_index: int,   # the value of the fixed axis (border row/col)
    ) -> int | None:
        """
        Find the nearest walkable block along a border strip.

        Parameters
        ----------
        map_name    : map constant string (e.g. "ROUTE_1")
        scan_coord  : the player's current coordinate on the scan axis
                      (used as origin for "closest" search)
        axis        : "x" → we iterate over x values on a fixed-y strip,
                      "y" → we iterate over y values on a fixed-x strip
        strip_index : the fixed value of the OTHER axis (the border row/col)

        Returns the nearest walkable coordinate on the scan axis, or None if
        none found.

        Examples
        --------
        # Top border (y=0), walking north — find walkable x closest to player_x:
        nx = rom_pass.nearest_walkable_on_strip("ROUTE_1", scan_coord=player_x,
                                                 axis="x", strip_index=0)

        # Left border (x=0), walking west — find walkable y closest to player_y:
        ny = rom_pass.nearest_walkable_on_strip("ROUTE_1", scan_coord=player_y,
                                                 axis="y", strip_index=0)
        """
        dims = self._get_dims().get(map_name)
        if dims is None:
            return None
        w, h = dims

        if axis == "x":
            candidates = range(w)
            def check(v):
                return self.is_passable(map_name, bx=v, by=strip_index)
        else:  # axis == "y"
            candidates = range(h)
            def check(v):
                return self.is_passable(map_name, bx=strip_index, by=v)

        # Sort candidate coordinates by distance from scan_coord
        sorted_cands = sorted(candidates, key=lambda v: abs(v - scan_coord))
        for v in sorted_cands:
            if check(v):
                return v
        return None
