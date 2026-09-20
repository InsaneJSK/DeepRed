"""Grouped checks for service decisions and bounded, validated input."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pyboy.utils import WindowEvent as E

from autonomous_controller.overworld_menu import OverworldMenu
from memory_state.sprite_names import object_name, sprite_name


def fixture(x, y, text):
    memory = bytearray(65536)
    memory[0xCC25], memory[0xCC24], memory[0xCC28] = x, y, 1
    pointer = 0xC3A0 + y * 20 + x
    memory[0xCC30], memory[0xCC31] = pointer % 256, pointer // 256
    memory[pointer] = 0xED
    gs = SimpleNamespace(
        mem=SimpleNamespace(read_byte=memory.__getitem__),
        dialog=text,
        map={"in_battle": False},
        items=[],
    )
    emulator = Mock()
    return OverworldMenu(emulator, gs), memory, emulator


def test_service_choices_and_labels(subtests):
    for x, y, text, kind, labels in (
        (1, 1, "BUY SELL QUIT", "shop", ["Buy", "Sell", "Quit"]),
        (12, 8, "HEAL CANCEL", "heal", ["Heal", "Cancel"]),
        (15, 8, "YES NO", "yes_no", ["Yes", "No"]),
    ):
        with subtests.test(menu=kind):
            menu, _, emulator = fixture(x, y, text)
            assert [o["name"] for o in menu.observe()["options"]] == labels
            assert menu.observe()["kind"] == kind
            assert menu.advance()  # Already at a choice: never press a button.
            emulator.send_input.assert_not_called()
    assert sprite_name(3) == "Professor Oak"
    assert sprite_name(38) == "Clerk"
    assert sprite_name(41) == "Nurse"
    assert sprite_name(255) == "Unknown sprite 255"
    assert object_name("VIRIDIAN_CITY", {"slot": 7, "picture_id": 11}).startswith("Old man")
    assert object_name("OTHER_MAP", {"slot": 7, "picture_id": 11}) == "Gambler"


def test_service_input_validation_and_bounds(subtests):
    menu, memory, emulator = fixture(12, 8, "HEAL CANCEL")
    for index in (-1, 2):
        with subtests.test(invalid_option=index), pytest.raises(ValueError):
            menu.choose(index)
    emulator.send_input.assert_not_called()
    with pytest.raises(RuntimeError, match="cursor"):
        menu.choose(1)  # Frozen cursor must fail without confirming.
    assert not any(c.args == (E.PRESS_BUTTON_A,) for c in emulator.send_input.call_args_list)
    assert emulator.tick.call_count <= 1200

    menu, memory, emulator = fixture(5, 4, "Take your time.")
    memory[0xC3A0 + 4 * 20 + 5] = 0xEC
    memory[0xC3A0 + 10 * 20 + 8] = 0xF1
    memory[0xCF96], memory[0xCF97] = 1, 99
    for amount in (0, 100):
        with subtests.test(invalid_quantity=amount), pytest.raises(ValueError):
            menu.quantity(amount)
    emulator.send_input.assert_not_called()
    assert menu.quantity(1)
    assert menu.observe() is None  # Stale quantity box is no longer actionable.
