"""
build_world_graph.py

Parses the pret/pokered disassembly to produce world_graph.json.

Usage:
    python autonomous_controller/build_world_graph.py --pokered pokered --output world_graph.json

Output: world_graph.json in the current directory.

world_graph.json schema:
{
  "maps": {
    "PALLET_TOWN": {
      "id": 0,
      "name": "PALLET_TOWN",
      "warps": [
        {
          "x": 5, "y": 5,
          "warp_index": 0,
          "dest_map": "REDS_HOUSE_1F",
          "dest_warp_index": 1
        }, ...
      ],
      "connections": {
        "north": {"map": "ROUTE_1", "offset": 0},
        "south": {"map": "ROUTE_21", "offset": 0}
      }
    },
    ...
  },
  "map_name_to_id": { "PALLET_TOWN": 0, ... },
  "map_id_to_name": { "0": "PALLET_TOWN", ... }
}
"""

import re
import json
import argparse
from pathlib import Path

# Parse map ID constants from constants/map_constants.asm
def parse_map_constants(pokered_root: Path) -> dict[str, int]:
    """
    Returns {MAP_NAME: integer_id} by parsing map_constants.asm.
    The file uses `map_const MAP_NAME, w, h` with running counter,
    and also `const MAP_NAME` style entries for the outdoor maps.
    We assign IDs in order of appearance (0-indexed), matching the game.
    """
    constants_file = pokered_root / "constants" / "map_constants.asm"
    if not constants_file.exists():
        raise FileNotFoundError(f"Cannot find {constants_file}")

    map_id: dict[str, int] = {}
    current_id = 0

    # Patterns:
    # map_const PALLET_TOWN, 5, 6   -> outdoor maps
    # map_const REDS_HOUSE_1F, 4, 4 -> indoor maps
    # The file assigns IDs sequentially; we just count them.
    map_const_re = re.compile(r'^\s*map_const\s+([A-Z0-9_]+)', re.IGNORECASE)

    with open(constants_file, encoding="utf-8") as f:
        for line in f:
            line = line.split(";")[0]
            m = map_const_re.match(line)
            if m:
                name = m.group(1).upper()
                map_id[name] = current_id
                current_id += 1

    return map_id

# Parse warp_event entries from data/maps/objects/*.asm

def _get_map_name(lines: list[str], warps_to_re: re.Pattern) -> str | None:
    for line in lines:
        m = warps_to_re.match(line)
        if m:
            return m.group(1).upper()
    return None


def _parse_warp_events(lines: list[str], warp_re: re.Pattern) -> list[dict]:
    warp_list = []
    for line in lines:
        m = warp_re.match(line)
        if m:
            warp_list.append({
                "warp_index": len(warp_list),
                "x": int(m.group(1)),
                "y": int(m.group(2)),
                "dest_map": m.group(3).upper(),
                "dest_warp_index": int(m.group(4)),
            })
    return warp_list


def parse_warps(pokered_root: Path) -> dict[str, list[dict]]:
    """
    Returns {MAP_NAME: [warp_event, ...]} by parsing data/maps/objects/*.asm.
    """
    objects_dir = pokered_root / "data" / "maps" / "objects"
    warps: dict[str, list[dict]] = {}

    warp_re = re.compile(
        r'^\s*warp_event\s+(\d+)\s*,\s*(\d+)\s*,\s*([A-Z0-9_]+)\s*,\s*(\d+)',
        re.IGNORECASE
    )
    warps_to_re = re.compile(
        r'^\s*def_warps_to\s+([A-Z0-9_]+)',
        re.IGNORECASE
    )

    for asm_file in sorted(objects_dir.glob("*.asm")):
        with open(asm_file, encoding="utf-8") as f:
            lines = [l.split(";")[0] for l in f]

        current_map = _get_map_name(lines, warps_to_re)
        if current_map is None:
            continue

        warps[current_map] = _parse_warp_events(lines, warp_re)

    return warps

# Parse connection entries from data/maps/headers/*.asm

def _parse_header_file(
    asm_file: Path,
    header_re: re.Pattern,
    conn_re: re.Pattern,
) -> dict[str, dict]:
    """
    Helper to parse a single header file,
    returning {MAP_NAME: {direction: {map: OTHER_MAP, offset: N}}}.
    """
    connections: dict[str, dict] = {}

    current_map = None
    map_conns: dict[str, dict] = {}

    with open(asm_file, encoding="utf-8") as f:
        for line in f:
            line_stripped = line.split(";")[0].strip()

            m_hdr = header_re.match(line_stripped)
            if m_hdr:
                current_map = m_hdr.group(1).upper()
                map_conns = {}
                continue

            m_conn = conn_re.match(line_stripped)
            if m_conn and current_map:
                map_conns[m_conn.group(1).lower()] = {
                    "map": m_conn.group(2).upper(),
                    "offset": int(m_conn.group(3)),
                }
                continue

            if "end_map_header" in line_stripped and current_map:
                if map_conns:
                    connections[current_map] = map_conns
                current_map = None

    return connections


def parse_connections(pokered_root: Path) -> dict[str, dict]:
    """
    Returns {MAP_NAME: {direction: {map: OTHER_MAP, offset: N}}} by parsing data/maps/headers/*.asm.
    """
    headers_dir = pokered_root / "data" / "maps" / "headers"
    if not headers_dir.exists():
        raise FileNotFoundError(f"Cannot find {headers_dir}")

    header_re = re.compile(
        r'^\s*map_header\s+\w+\s*,\s*([A-Z0-9_]+)', re.IGNORECASE
    )
    conn_re = re.compile(
        r'^\s*connection\s+(north|south|east|west)\s*,\s*\w+\s*,\s*([A-Z0-9_]+)\s*,\s*(-?\d+)',
        re.IGNORECASE
    )

    connections: dict[str, dict] = {}

    for asm_file in sorted(headers_dir.glob("*.asm")):
        file_conns = _parse_header_file(asm_file, header_re, conn_re)
        connections.update(file_conns)

    return connections

# Step 4: Assemble world_graph.json

def build_graph(pokered_root: Path) -> dict:
    """
    Returns the full graph dict to be serialized as world_graph.json.
    """
    print("[1/4] Parsing map ID constants...")
    map_name_to_id = parse_map_constants(pokered_root)
    map_id_to_name = {v: k for k, v in map_name_to_id.items()}
    print(f"      Found {len(map_name_to_id)} maps.")

    print("[2/4] Parsing warp events from objects/...")
    all_warps = parse_warps(pokered_root)
    all_warps = resolve_last_map(all_warps)
    print(f"      Found warp data for {len(all_warps)} maps.")

    print("[3/4] Parsing connections from headers/...")
    all_connections = parse_connections(pokered_root)
    print(f"      Found connection data for {len(all_connections)} maps.")

    print("[4/4] Assembling graph...")

    # Union of all known map names
    all_map_names = (
        set(map_name_to_id.keys())
        | set(all_warps.keys())
        | set(all_connections.keys())
    )

    maps: dict[str, dict] = {}
    for name in sorted(all_map_names):
        entry = {
            "id": map_name_to_id.get(name, -1),   # -1 = not in constants
            "name": name,
            "warps": all_warps.get(name, []),
            "connections": all_connections.get(name, {}),
        }
        maps[name] = entry

    # Validate: check all warp destinations exist
    missing_dests: set[str] = set()
    for _, entry in maps.items():
        for warp in entry["warps"]:
            dest = warp["dest_map"]
            if dest not in maps:
                missing_dests.add(dest)
    if missing_dests:
        print(f"  [WARN] {len(missing_dests)} unknown warp destinations: {missing_dests}")

    graph = {
        "maps": maps,
        "map_name_to_id": map_name_to_id,
        "map_id_to_name": {str(k): v for k, v in map_id_to_name.items()},
    }

    return graph

def resolve_last_map(warps: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """
    Replace LAST_MAP destinations with the actual map name.
    
    LAST_MAP is used for exit warps (doors to outside). The real destination
    is the map that has a warp pointing INTO this map — i.e. if MAP_B has a
    warp to MAP_A, then MAP_A's LAST_MAP warps resolve to MAP_B.
    """
    # Build reverse lookup: for each map, which maps warp into it?
    # incoming[MAP_A] = set of maps that have a warp_event pointing to MAP_A
    incoming: dict[str, set[str]] = {}
    for src_map, warp_list in warps.items():
        for warp in warp_list:
            dest = warp["dest_map"]
            if dest == "LAST_MAP":
                continue
            if dest not in incoming:
                incoming[dest] = set()
            incoming[dest].add(src_map)

    # Resolve LAST_MAP
    for src_map, warp_list in warps.items():
        for warp in warp_list:
            if warp["dest_map"] == "LAST_MAP":
                candidates = incoming.get(src_map, set())
                if len(candidates) == 1:
                    warp["dest_map"] = next(iter(candidates))
                elif len(candidates) > 1:
                    warp["dest_map"] = sorted(candidates)[0]
                    print(f"[WARN] {src_map} LAST_MAP ambiguous: {candidates},\
                         chose {warp['dest_map']}")
                else:
                    print(f"[WARN] {src_map} LAST_MAP unresolvable — no incoming warps found")

    return warps

# Entrypoint

def main():
    """Main entrypoint: parse args, build graph, write JSON."""
    parser = argparse.ArgumentParser(
        description="Build world_graph.json from the pret/pokered disassembly."
    )
    parser.add_argument(
        "--pokered",
        required=True,
        help="Path to the root of the cloned pret/pokered repo",
    )
    parser.add_argument(
        "--output",
        default="world_graph.json",
        help="Output file path (default: world_graph.json)",
    )
    args = parser.parse_args()

    pokered_root = Path(args.pokered).resolve()
    if not pokered_root.exists():
        raise SystemExit(f"ERROR: pokered root not found: {pokered_root}")

    graph = build_graph(pokered_root)

    output_path = Path(args.output)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2)

    total_warps = sum(len(m["warps"]) for m in graph["maps"].values())
    total_conns = sum(len(m["connections"]) for m in graph["maps"].values())
    print(f"\nDone! Written to {output_path}")
    print(f"  Maps:        {len(graph['maps'])}")
    print(f"  Warp edges:  {total_warps}")
    print(f"  Conn edges:  {total_conns}")


if __name__ == "__main__":
    main()
