"""Versioned navigation data, independent of the optional disassembly checkout."""

import hashlib
import json
from pathlib import Path

DEFAULT_BUNDLE = Path(__file__).resolve().parents[1] / "game_data" / "pokemon_red.json"


def payload_digest(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_bundle(path=None):
    path = DEFAULT_BUNDLE if path is None else Path(path)
    with path.open(encoding="utf-8") as stream:
        bundle = json.load(stream)
    if bundle.get("schema_version") != 1:
        raise ValueError(f"Unsupported game-data schema in {path}")
    data = bundle["data"]
    if payload_digest(data) != bundle["metadata"]["data_sha256"]:
        raise ValueError(f"Game-data checksum mismatch in {path}")
    if data["graph"].get("schema_version") != 2:
        raise ValueError("Unsupported bundled world-graph schema")
    terrain = data["terrain"]
    for name, grid in terrain["tiles"].items():
        width, height = terrain["dimensions"][name]
        if grid and (len(grid) != height * 2 or any(len(row) != width * 2 for row in grid)):
            raise ValueError(f"Invalid bundled terrain dimensions for {name}")
    return data


def validate_rom(rom_path, bundle_path=None):
    """Fail early when static data and the user's cartridge do not match."""
    path = DEFAULT_BUNDLE if bundle_path is None else Path(bundle_path)
    load_bundle(path)
    metadata = json.loads(path.read_text(encoding="utf-8"))["metadata"]
    actual = hashlib.sha1(Path(rom_path).read_bytes()).hexdigest()
    if actual != metadata["rom_sha1"]:
        raise ValueError("ROM does not match the supported Pokemon Red game-data bundle")
