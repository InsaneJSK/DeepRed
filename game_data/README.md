# Pokemon Red navigation data

[Project overview](../README.md) · [Navigation design](../NAVIGATION.md) · [Usage and testing](../docs/USAGE.md)

`pokemon_red.json` is the checked-in runtime bundle. It contains map tile IDs,
dimensions, walkability rules, tile-pair restrictions, warps, connections, and map
IDs. It contains no ROM image or player saves. Changing game state still comes
from live emulator RAM; this file does not supply live offscreen NPC positions.

Source: [pret/pokered](https://github.com/pret/pokered), revision
`e82e0ead5066c006ce43a8a4b3a5b7285a037143`. Supported Pokemon Red ROM SHA-1:
`ea9bcae617fdf159b045185467ae58b2e4a48b9a`.

The bundle records its schema version, source revision, supported ROM checksum,
and a SHA-256 digest of the canonical data payload. Runtime loading validates
the schema, digest, and grid dimensions. File paths are resolved relative to
the installed source tree, not the working directory.

## Optional regeneration

Only maintainers regenerating the data need a clean checkout of the pinned
source. It can live outside this repository. From the DeepRed project root:

```powershell
uv run python -m tools.export_game_data --pokered C:/path/to/pokered
uv run pytest
```

The exporter rejects unexpected revisions or modified tracked source files.
An intentional upstream migration requires `--expected-revision COMMIT` and
review of the regenerated data plus gameplay regressions. Output is deterministic.
To compare without replacing the bundled file, pass `--output status/candidate.json`.
The optional source parity test uses an ignored local `pokered/` checkout, when
present. Normal operation never imports `tools/import_terrain.py` or runs Git.

`autonomous_controller/build_world_graph.py` remains an offline importer helper;
its standalone JSON output is no longer used by the runtime. Controller and
terrain constructors accept an optional bundle path rather than a source-repo
or standalone-graph path.

## Preserved limitations

Static geometry retains the existing movement limitations (no generalized
Surf/Cut/Strength/ledge planning or dynamic terrain). Special elevator/scripted
return destinations remain subject to the graph's existing limitations.

The source declares UNDERGROUND_PATH_NORTH_SOUTH as 4x24 blocks but supplies a
4x23 block file. The previous reader raised on this map. It is explicitly listed
under `unsupported_maps` and treated as unavailable terrain; no bytes are invented.
The optional parity check verifies that this exception remains explicit and
that all other exported terrain matches the source parser.
