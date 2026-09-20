"""Bundle integrity and independence from the optional source checkout."""

import json
from pathlib import Path

import pytest

from autonomous_controller.game_data import DEFAULT_BUNDLE, load_bundle, validate_rom
from autonomous_controller.walkable_map import RomPassability
from autonomous_controller.world_graph import WorldGraph


def test_navigation_needs_neither_source_checkout_nor_generated_graph(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        assert "pokered" not in path.parts
        assert path.name != "world_graph.json"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    terrain, graph = RomPassability(), WorldGraph()
    assert terrain.is_passable("REDS_HOUSE_2F", 3, 6)
    assert graph.terrain_route("ROUTE_2", "VIRIDIAN_FOREST", (7, 71), terrain) == [
        "ROUTE_2",
        "VIRIDIAN_FOREST_SOUTH_GATE",
        "VIRIDIAN_FOREST",
    ]


@pytest.mark.parametrize("mutation, message", [("version", "schema"), ("data", "checksum")])
def test_rejects_incompatible_or_corrupt_bundle(tmp_path, mutation, message):
    bundle = json.loads(DEFAULT_BUNDLE.read_text())
    if mutation == "version":
        bundle["schema_version"] = 99
    else:
        bundle["data"]["terrain"]["tiles"]["REDS_HOUSE_2F"][0][0] += 1
    path = tmp_path / "data.json"
    path.write_text(json.dumps(bundle))
    with pytest.raises(ValueError, match=message):
        load_bundle(path)


def test_rejects_wrong_rom_before_starting_game(tmp_path):
    rom = tmp_path / "wrong.gb"
    rom.write_bytes(b"not the supported cartridge")
    with pytest.raises(ValueError, match="ROM does not match"):
        validate_rom(rom)


@pytest.mark.source_data
def test_all_bundled_terrain_and_graph_match_pinned_source(tmp_path):
    source = Path(__file__).resolve().parents[1] / "pokered"
    if not (source / "constants/map_constants.asm").exists():
        pytest.skip("Optional importer parity check requires the pinned source checkout")
    from tools.export_game_data import export_bundle
    from tools.import_terrain import SourceTerrain

    regenerated = tmp_path / "regenerated.json"
    export_bundle(source, regenerated)
    assert regenerated.read_bytes() == DEFAULT_BUNDLE.read_bytes()
    source_terrain, bundled = SourceTerrain(source), RomPassability()
    assert source_terrain.dims == bundled.dims
    assert source_terrain.headers == bundled.headers
    assert source_terrain.collision == bundled.collision
    assert source_terrain.pairs == bundled.pairs
    unsupported = load_bundle()["terrain"]["unsupported_maps"]
    assert set(unsupported) == {"UNDERGROUND_PATH_NORTH_SOUTH"}
    for name in source_terrain.dims:
        if name in unsupported:
            with pytest.raises(ValueError, match="dimensions"):
                source_terrain.tiles(name)
            assert bundled.tiles(name) == ()
        else:
            assert source_terrain.tiles(name) == bundled.tiles(name)
