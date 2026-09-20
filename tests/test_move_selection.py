"""Move-list regression: wrapping and one-based cursor follow the game engine.

Reference: pokered e82e0ea, engine/battle/core.asm, MoveSelectionMenu and
SelectMenuItem_CursorUp/Down. No source checkout or ROM is required by this test.
This isolates the already-open move list; it does not validate opening FIGHT.
"""

from types import SimpleNamespace

import pytest
from pyboy.utils import WindowEvent

from autonomous_controller.battle_controller import BattleController


class MoveList:
    """Small model of the game's menu contract, independent of navigation logic."""

    def __init__(self, count, selected):
        self.count = count
        self.memory = bytearray(65536)
        self.memory[0xCC26] = selected + 1  # live menu row
        self.memory[0xCC28] = count + 1  # bottom sentinel used for wrapping
        self.memory[0xCC2A] = 0  # unrelated wLastMenuItem, not our cursor
        self.presses = []

    @property
    def selected(self):
        return self.memory[0xCC26] - 1

    def press(self, button):
        self.presses.append(button)
        if button == WindowEvent.PRESS_ARROW_UP:
            self.memory[0xCC26] = (self.selected - 1) % self.count + 1
        elif button == WindowEvent.PRESS_ARROW_DOWN:
            self.memory[0xCC26] = (self.selected + 1) % self.count + 1
        else:
            raise AssertionError(f"Move navigation sent unexpected button {button}")

    def controller(self):
        state = SimpleNamespace(mem=SimpleNamespace(read_byte=lambda addr: self.memory[addr]))
        controller = BattleController(None, state)
        controller._press = self.press
        return controller


@pytest.mark.parametrize(
    "count,start,target", [(2, 0, 0), (2, 1, 0), (3, 0, 2), (4, 0, 0), (4, 3, 1)]
)
def test_move_navigation_reaches_requested_slot(count, start, target):
    menu = MoveList(count, start)
    menu.controller()._navigate_move_menu(target)
    assert menu.selected == target, f"Requested slot {target}, cursor landed on {menu.selected}"


def test_repeated_first_move_does_not_alternate():
    menu = MoveList(2, 0)
    controller = menu.controller()
    selected = []
    for _ in range(4):
        controller._navigate_move_menu(0)
        selected.append(menu.selected)  # engine remembers selection next turn
    assert selected == [0, 0, 0, 0]


def test_move_cursor_reads_live_one_based_row():
    menu = MoveList(4, 2)
    assert menu.controller().move_cursor() == 2


@pytest.mark.parametrize("target", [-1, 2])
def test_nonexistent_move_slot_does_not_send_inputs(target):
    menu = MoveList(2, 0)
    with pytest.raises(ValueError):
        menu.controller()._navigate_move_menu(target)
    assert menu.presses == []


def test_unresponsive_cursor_fails_with_bounded_inputs():
    menu = MoveList(2, 1)
    controller = menu.controller()
    controller._press = menu.presses.append  # game ignores directional input
    with pytest.raises(RuntimeError, match="cursor"):
        controller._navigate_move_menu(0)
    assert len(menu.presses) <= 4
