"""Move-menu choices and failure handling, grouped by behavior."""

from types import SimpleNamespace

import pytest
from pyboy.utils import WindowEvent

from autonomous_controller.battle_controller import BattleController


class MenuState:
    """Menu RAM fixture with engine-style row/column and scrolling semantics."""

    def __init__(self, menu="main", cursor=0, scroll=0):
        self.memory = bytearray(65536)
        self.memory[0xD057] = 2
        self.memory[0xD163] = 2
        self.memory[0xD16D] = self.memory[0xD199] = 20
        self.memory[0xCC30:0xCC32] = (0xB4, 0xC3)
        self.memory[0xC3B4] = 0xED
        self.memory[0xCC26] = cursor
        self.memory[0xCC36] = scroll
        geometry, text = {
            "main": ((9, 14), "FIGHT PKMN ITEM RUN"),
            "party": ((0, 1), "Choose a POKéMON."),
            "party_actions": ((12, 12), "SWITCH STATS CANCEL"),
            "bag": ((5, 4), "POTION x5 CANCEL"),
            "moves": ((5, 12), "TYPE/ NORMAL SCRATCH"),
            "item_moves": ((5, 7), "WHICH TECHNIQUE? SCRATCH"),
            "next_pokemon": ((14, 10), "Use next POKéMON? YES NO"),
            "nickname": ((14, 10), "Give a NICKNAME? YES NO"),
        }[menu]
        self.memory[0xCC25], self.memory[0xCC24] = geometry
        self.memory[0xCC37] = int(menu == "bag")
        self.state = SimpleNamespace(
            dialog=text, mem=SimpleNamespace(read_byte=self.memory.__getitem__)
        )
        self.controller = BattleController(None, self.state)
        self.presses = []

    def press_main(self, button):
        self.presses.append(button)
        if button == WindowEvent.PRESS_ARROW_RIGHT:
            self.memory[0xCC25] = 15
        elif button == WindowEvent.PRESS_ARROW_LEFT:
            self.memory[0xCC25] = 9
        elif button in (WindowEvent.PRESS_ARROW_UP, WindowEvent.PRESS_ARROW_DOWN):
            self.memory[0xCC26] ^= 1  # rows wrap; blind UP is not a reset
        else:
            raise AssertionError("Navigation must not confirm the menu")


def test_main_menu_choices_and_readiness(subtests):
    for start in range(4):
        for target in range(4):
            with subtests.test(start=start, target=target):
                menu = MenuState(cursor=start // 2)
                menu.memory[0xCC25] = 9 if start % 2 == 0 else 15
                menu.controller._press = menu.press_main
                menu.controller._navigate_main_menu(target)
                assert menu.memory[0xCC26] == target // 2
                assert menu.memory[0xCC25] == (9 if target % 2 == 0 else 15)
                assert len(menu.presses) == (start // 2 != target // 2) + (start % 2 != target % 2)
    for state in (
        "main",
        "party",
        "party_actions",
        "bag",
        "moves",
        "item_moves",
        "next_pokemon",
        "nickname",
    ):
        with subtests.test(state=state):
            menu = MenuState(state)
            assert menu.controller.menu_state() == state
            menu.memory[0xC3B4] = 0xEC  # stale geometry plus an unfilled arrow is not ready
            assert menu.controller.menu_state() == "text"
            assert not menu.controller.is_player_turn()


def test_party_and_bag_cursor_validation(subtests):
    for start, target in ((0, 7), (7, 0), (8, 3)):
        with subtests.test(menu="bag", start=start, target=target):
            menu = MenuState("bag", cursor=min(start, 2), scroll=max(0, start - 2))

            def press(button):
                menu.presses.append(button)
                absolute = menu.memory[0xCC26] + menu.memory[0xCC36]
                absolute += 1 if button == WindowEvent.PRESS_ARROW_DOWN else -1
                assert 0 <= absolute <= 8
                menu.memory[0xCC26] = min(absolute, 2)
                menu.memory[0xCC36] = max(0, absolute - 2)

            menu.controller._press = press
            menu.controller._navigate_list(target, 8, "bag", scroll=True)
            assert menu.memory[0xCC26] + menu.memory[0xCC36] == target
            assert len(menu.presses) == abs(start - target)
    for state in ("main", "bag", "party"):
        with subtests.test(menu=state, scenario="unresponsive_cursor"):
            menu = MenuState(state)
            menu.controller._press = menu.presses.append
            with pytest.raises(RuntimeError, match="cursor"):
                if state == "main":
                    menu.controller._navigate_main_menu(3)
                else:
                    menu.controller._navigate_list(1, 2, state, scroll=state == "bag")
            assert len(menu.presses) <= 5
            assert WindowEvent.PRESS_BUTTON_A not in menu.presses
    for target in (-1, 0, 2, True):
        with subtests.test(menu="party", invalid_target=target):
            menu = MenuState()
            menu.controller._press = menu.presses.append
            with pytest.raises(ValueError):
                menu.controller.switch(target)
            assert not menu.presses
    with subtests.test(menu="party", scenario="fainted_reserve"):
        menu = MenuState()
        menu.memory[0xD199] = 0
        assert menu.controller.available_switches() == []
        with pytest.raises(ValueError):
            menu.controller.switch(1)


def test_bag_targets_rejected_before_inputs(subtests):
    cases = [
        (20, -1, 1, None),
        (20, 1, 1, None),
        (20, 0, None, None),
        (20, 0, 2, None),
        (20, 0, 1, 0),
        (0x50, 0, 1, None),
        (0x50, 0, 1, 2),
        (4, 0, None, None),
        (5, 0, None, None),
    ]
    for item, bag, party, move in cases:
        with subtests.test(item=item, bag=bag, party=party, move=move):
            menu = MenuState()
            menu.memory[0xD31D:0xD320] = (1, item, 5)
            menu.memory[0xD19F:0xD1A1] = (10, 45)
            menu.controller._press = menu.presses.append
            with pytest.raises(ValueError):
                menu.controller.use_item(bag, party, move)
            assert not menu.presses


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


def test_move_menu_selections(subtests):
    for count, start, target in [(2, 0, 0), (2, 1, 0), (3, 0, 2), (4, 0, 0), (4, 3, 1)]:
        with subtests.test(count=count, start=start, target=target):
            menu = MoveList(count, start)
            menu.controller()._navigate_move_menu(target)
            assert menu.selected == target, (
                f"Requested slot {target}, cursor landed on {menu.selected}"
            )

    with subtests.test(scenario="repeated_first_move_does_not_alternate"):
        menu = MoveList(2, 0)
        controller = menu.controller()
        selected = []
        for _ in range(4):
            controller._navigate_move_menu(0)
            selected.append(menu.selected)  # engine remembers selection next turn
        assert selected == [0, 0, 0, 0]

    with subtests.test(scenario="move_cursor_reads_live_one_based_row"):
        menu = MoveList(4, 2)
        assert menu.controller().move_cursor() == 2


def test_move_menu_rejects_invalid_or_unresponsive_selection(subtests):
    for target in [-1, 2]:
        with subtests.test(target=target):
            menu = MoveList(2, 0)
            with pytest.raises(ValueError):
                menu.controller()._navigate_move_menu(target)
            assert menu.presses == []

    with subtests.test(scenario="unresponsive_cursor_fails_with_bounded_inputs"):
        menu = MoveList(2, 1)
        controller = menu.controller()
        controller._press = menu.presses.append  # game ignores directional input
        with pytest.raises(RuntimeError, match="cursor"):
            controller._navigate_move_menu(0)
        assert len(menu.presses) <= 4
