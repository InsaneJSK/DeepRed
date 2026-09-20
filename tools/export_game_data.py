"""Build the checked-in data bundle from an explicitly pinned pokered checkout."""

import argparse
import json
import subprocess
from pathlib import Path

from autonomous_controller.build_world_graph import build_graph
from autonomous_controller.game_data import DEFAULT_BUNDLE, payload_digest
from tools.import_terrain import SourceTerrain

SOURCE_REVISION = "e82e0ead5066c006ce43a8a4b3a5b7285a037143"


def export_bundle(source, output=DEFAULT_BUNDLE, expected_revision=SOURCE_REVISION):
    source = Path(source).resolve()

    def git(*args):
        return subprocess.check_output(
            ["git", "-c", f"safe.directory={source.as_posix()}", "-C", str(source), *args],
            text=True,
        ).strip()

    revision = git("rev-parse", "HEAD")
    if revision != expected_revision:
        raise ValueError(f"Expected pokered revision {expected_revision}, found {revision}")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Export requires a clean tracked pokered checkout")
    terrain = SourceTerrain(source)
    tiles, unsupported = {}, {}
    for name in sorted(terrain.dims):
        try:
            tiles[name] = terrain.tiles(name)
        except ValueError as exc:
            # Known source inconsistency: 4x23 bytes but a declared 4x24 map.
            # The old loader raised here; don't invent terrain during migration.
            if name != "UNDERGROUND_PATH_NORTH_SOUTH":
                raise
            tiles[name] = ()
            unsupported[name] = str(exc)
    data = {
        "graph": build_graph(source),
        "terrain": {
            "dimensions": terrain.dims,
            "headers": terrain.headers,
            "collision": {name: sorted(values) for name, values in terrain.collision.items()},
            "pairs": {
                name: sorted(sorted(pair) for pair in pairs)
                for name, pairs in terrain.pairs.items()
            },
            "tiles": tiles,
            "unsupported_maps": unsupported,
        },
    }
    rom_sha1 = next(
        line.split()[0]
        for line in (source / "roms.sha1").read_text().splitlines()
        if line.endswith("*pokered.gbc")
    )
    bundle = {
        "schema_version": 1,
        "metadata": {
            "source": "https://github.com/pret/pokered",
            "source_revision": revision,
            "rom_sha1": rom_sha1,
            "data_sha256": payload_digest(data),
            "coordinates": "16-pixel player steps; dimensions stored in 32-pixel blocks",
        },
        "data": data,
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(bundle, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    print(f"Exported {len(terrain.dims)} map definitions to {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pokered", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--expected-revision", default=SOURCE_REVISION)
    args = parser.parse_args()
    export_bundle(args.pokered, args.output, args.expected_revision)


if __name__ == "__main__":
    main()
