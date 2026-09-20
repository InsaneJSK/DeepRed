"""Runtime data integrity, memory decoding and optional importer parity."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autonomous_controller.game_data import DEFAULT_BUNDLE, load_bundle, validate_rom
from autonomous_controller.walkable_map import RomPassability
from autonomous_controller.world_graph import WorldGraph
from memory_state.misc_info import MiscInfo


def test_bundle_independence_and_validation(monkeypatch, tmp_path, subtests):
    with subtests.test(scenario="navigation_needs_neither_source_checkout_nor_generated_graph"):
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

    for mutation, message in [("version", "schema"), ("data", "checksum")]:
        with subtests.test(mutation=mutation, message=message):
            bundle = json.loads(DEFAULT_BUNDLE.read_text())
            if mutation == "version":
                bundle["schema_version"] = 99
            else:
                bundle["data"]["terrain"]["tiles"]["REDS_HOUSE_2F"][0][0] += 1
            path = tmp_path / "data.json"
            path.write_text(json.dumps(bundle))
            with pytest.raises(ValueError, match=message):
                load_bundle(path)

    with subtests.test(scenario="rejects_wrong_rom_before_starting_game"):
        rom = tmp_path / "wrong.gb"
        rom.write_bytes(b"not the supported cartridge")
        with pytest.raises(ValueError, match="ROM does not match"):
            validate_rom(rom)


def test_memory_names_and_currency(subtests):
    with subtests.test(scenario="names_are_bounded_and_terminated_and_coins_are_decimal"):
        memory = bytearray(65536)
        memory[0xD158:0xD163] = bytes([0x80, 0x50] + [0x99] * 9)
        memory[0xD34A:0xD355] = bytes(range(0x80, 0x8A)) + bytes([0x50])
        info = MiscInfo(SimpleNamespace(memory=memory))
        assert info.names == ("A", "ABCDEFGHIJ")
        for raw, expected in [(b"\x00\x00", 0), (b"\x12\x34", 1234), (b"\x99\x99", 9999)]:
            memory[0xD5A4:0xD5A6] = raw
            assert info.read_coins == expected


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
