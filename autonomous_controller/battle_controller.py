"""
autonomous_controller/battle_controller.py

BattleController — RAM-driven battle menu navigation for Pokemon Red.

State combines RAM fields with text decoded from the RAM tilemap.

RAM addresses (verified against pret/pokered wram.asm + diagnostic):
    0xCC26  wCurrentMenuItem        current cursor in the active menu (0-indexed)
    0xCC25  wTopMenuItemX          main-menu column (9=left, 15=right)
    0xCC2F  wPlayerMonNumber        active party Pokémon slot (0-indexed)
    0xCC36  wListScrollOffset       first visible bag item
    0xD057  wIsInBattle             0=none, 1=wild, 2=trainer
    0xD05A  wBattleType             0=normal, 1=old man, 2=safari

Main battle menu layout (2×2 grid):
    FIGHT(0) | PKMN(1)
    ----------+--------
    ITEM(2)  | RUN(3)

    Row  = index // 2   (0=top, 1=bottom)
    Col  = index  % 2   (0=left, 1=right)
    These are API indices; the engine stores the row and column separately.

Usage (from AI agent):
    bc = BattleController(pyboy, gs)
    bc.fight(move_index=0)          # use first move in move list
    bc.wait_for_turn()              # block until next player turn
    bc.run()                        # try to run
    bc.switch(party_index=1)        # switch to second party slot
    bc.use_item(bag_index=0)        # use first item in bag
"""

from pyboy.utils import WindowEvent

from autonomous_controller.emulator_session import FrameBudget, FrameLimitReached

# RAM addresses (verified against pret/pokered + live diagnostic)
_MENU_CURSOR = 0xCC26  # wCurrentMenuItem  — cursor in current menu
_MOVE_CURSOR = 0xCC26  # live move row is one-based
_PLAYER_MON_SLOT = 0xCC2F  # wPlayerMonNumber
_IS_IN_BATTLE = 0xD057  # wIsInBattle: 0=none, 1=wild, 2=trainer
_BATTLE_TYPE = 0xD05A  # wBattleType: 0=normal, 1=old man, 2=safari

# Main battle menu option indices
FIGHT = 0
PKMN = 1
ITEM = 2
RUN = 3

# How often to advance battle text with B, avoiding accidental confirmations.
_TEXT_ADVANCE_EVERY = 20


class BattleController:
    """
    RAM-driven navigation of Pokemon Red battle menus.
    Menu identity uses RAM geometry, a live cursor, and decoded RAM text.

    last_result describes item/switch outcomes: used, caught, not_caught,
    switched, rejected, cancelled, or timeout. No action writes game memory.
    """

    # Ticks to hold a button press
    PRESS_FRAMES = 3
    # Ticks to settle between presses
    SETTLE_FRAMES = 8
    # Ticks to wait for the screen to settle after opening a sub-menu
    SUBMENU_SETTLE = 30
    # Max ticks for wait_for_turn() (~10 s at 60 fps — covers long animations)
    TURN_TIMEOUT = 600

    def __init__(self, pyboy, game_state):
        self.pyboy = pyboy
        self.gs = game_state
        self.last_result = None

        self._release_map = {
            WindowEvent.PRESS_ARROW_UP: WindowEvent.RELEASE_ARROW_UP,
            WindowEvent.PRESS_ARROW_DOWN: WindowEvent.RELEASE_ARROW_DOWN,
            WindowEvent.PRESS_ARROW_LEFT: WindowEvent.RELEASE_ARROW_LEFT,
            WindowEvent.PRESS_ARROW_RIGHT: WindowEvent.RELEASE_ARROW_RIGHT,
            WindowEvent.PRESS_BUTTON_A: WindowEvent.RELEASE_BUTTON_A,
            WindowEvent.PRESS_BUTTON_B: WindowEvent.RELEASE_BUTTON_B,
        }

    # RAM helpers

    def _r(self, addr: int) -> int:
        """Read one byte from WRAM."""
        return self.gs.mem.read_byte(addr)

    def _tick(self, n: int = 1) -> None:
        for _ in range(n):
            self.pyboy.tick()

    def _press(self, btn: WindowEvent) -> None:
        """Hold *btn* for PRESS_FRAMES ticks then release and settle."""
        self.pyboy.send_input(btn)
        try:
            self._tick(self.PRESS_FRAMES)
        finally:
            self.pyboy.send_input(self._release_map[btn])
        self._tick(self.SETTLE_FRAMES)

    def _press_a(self) -> None:
        self._press(WindowEvent.PRESS_BUTTON_A)

    def _press_b(self) -> None:
        self._press(WindowEvent.PRESS_BUTTON_B)

    # State queries

    def is_in_battle(self) -> bool:
        """True while the battle flag is set."""
        return self._r(_IS_IN_BATTLE) != 0

    def is_wild_battle(self) -> bool:
        """True when fighting a wild Pokémon (wIsInBattle == 1)."""
        return self._r(_IS_IN_BATTLE) == 1

    def is_player_turn(self) -> bool:
        """True only when the main battle menu has an actionable cursor."""
        return self.menu_state() == "main"

    def menu_cursor(self) -> int:
        """Current cursor position in the active menu (0-indexed)."""
        return self._r(_MENU_CURSOR)

    def move_cursor(self) -> int:
        """Current move slot cursor in the FIGHT sub-menu (0-indexed)."""
        return self._r(_MOVE_CURSOR) - 1

    def active_mon_slot(self) -> int:
        """Index of the player's currently active Pokémon (0-indexed)."""
        return self._r(_PLAYER_MON_SLOT)

    def menu_state(self) -> str:
        """Return the actionable menu, or text/ended while no choice is ready."""
        if not self.is_in_battle():
            return "ended"
        # Menu RAM survives animations. Require its filled cursor to be drawn too.
        pointer = self._r(0xCC30) | self._r(0xCC31) << 8
        if not 0xC3A0 <= pointer < 0xC508 or self._r(pointer) != 0xED:
            return "text"
        text = " ".join(self.gs.dialog.upper().split())
        x, y = self._r(0xCC25), self._r(0xCC24)
        if "YES" in text and "NO" in text:
            if "NICKNAME" in text:
                return "nickname"
            if "USE NEXT" in text:
                return "next_pokemon"
            return "yes_no"
        if (x, y) in ((9, 14), (15, 14)) and "FIGHT" in text and "RUN" in text:
            return "main"
        if (x, y) == (12, 12) and "STATS" in text and "CANCEL" in text:
            return "party_actions"
        if (x, y) == (0, 1) and ("CHOOSE" in text or "WHICH" in text):
            return "party"
        if (x, y) == (5, 4) and self._r(0xCC37):
            return "bag"
        if (x, y) == (5, 12) and "TYPE/" in text:
            return "moves"
        if (x, y) == (5, 7) and "TECHNIQUE" in text:
            return "item_moves"
        return "text"

    def available_switches(self) -> list[int]:
        """Healthy reserve slots; read numeric HP rather than formatted party text."""
        count = self._r(0xD163)
        if not 1 <= count <= 6:
            return []
        return [i for i in range(count) if i != self.active_mon_slot() and self._party_hp(i)]

    def _party_hp(self, slot: int) -> int:
        address = 0xD16C + 44 * slot
        return self._r(address) * 256 + self._r(address + 1)

    def needs_switch(self) -> bool:
        """A replacement decision is ready; waiting must not cancel this menu."""
        state = self.menu_state()
        return state == "next_pokemon" or (
            state == "party" and not self._party_hp(self.active_mon_slot())
        )

    @staticmethod
    def _validate_index(index: int, count: int, label: str) -> None:
        if type(index) is not int or not 0 <= index < count:
            raise ValueError(f"{label} index must be between 0 and {count - 1}, got {index}")

    def _wait_menu(self, expected: str, timeout: int = 600) -> bool:
        """Wait without confirming any choice; include a short stability interval."""
        stable = 0
        for _ in range(timeout):
            self._tick()
            stable = stable + 1 if self.menu_state() == expected else 0
            if stable >= self.SETTLE_FRAMES:
                return True
            if not self.is_in_battle():
                return False
        self.last_result = "timeout"
        return False

    def _navigate_list(self, target: int, count: int, state: str, *, scroll=False) -> None:
        """Move one step at a time and never confirm an unverified cursor."""
        self._validate_index(target, count, state)
        for _ in range(count + 3):
            if self.menu_state() != state:
                raise RuntimeError(f"Expected {state} menu")
            current = self.menu_cursor() + (self._r(0xCC36) if scroll else 0)
            maximum = count if scroll else count - 1  # bag may start on CANCEL
            if not 0 <= current <= maximum:
                raise RuntimeError(f"Invalid {state} cursor {current}")
            if current == target:
                return
            self._press(
                WindowEvent.PRESS_ARROW_DOWN if current < target else WindowEvent.PRESS_ARROW_UP
            )
        raise RuntimeError(f"{state} cursor did not reach requested slot {target}")

    # Waiting

    def wait_for_turn(self, timeout: int | None = None) -> bool:
        """Wait for a battle or forced-switch decision within a total frame budget."""
        budget = FrameBudget(self.pyboy, self.TURN_TIMEOUT if timeout is None else timeout)
        elapsed = 0
        try:
            while budget.remaining:
                budget.tick()
                if not self.is_in_battle():
                    return False
                if self.menu_state() == "main" or self.needs_switch():
                    budget.tick(self.SUBMENU_SETTLE)
                    if self.menu_state() == "main" or self.needs_switch():
                        return True
                elif self.menu_state() not in ("text", "nickname"):
                    # A caller owns this choice; never back out or select on its behalf.
                    return False
                elif elapsed % _TEXT_ADVANCE_EVERY == 0:
                    budget.press(
                        WindowEvent.PRESS_BUTTON_B,
                        WindowEvent.RELEASE_BUTTON_B,
                        self.PRESS_FRAMES,
                        self.SETTLE_FRAMES,
                    )
                elapsed += 1
        except FrameLimitReached:
            pass
        return False

    def clear_post_battle_text(self, timeout: int = 3000) -> bool:
        """Return whether overworld text cleared within the total frame budget."""
        budget = FrameBudget(self.pyboy, timeout)
        stable = elapsed = 0
        try:
            while budget.remaining:
                budget.tick()
                if self.is_in_battle():
                    return False
                if self.gs.dialog.strip():
                    stable = 0
                    if elapsed % _TEXT_ADVANCE_EVERY == 0:
                        budget.press(
                            WindowEvent.PRESS_BUTTON_B,
                            WindowEvent.RELEASE_BUTTON_B,
                            self.PRESS_FRAMES,
                            self.SETTLE_FRAMES,
                        )
                else:
                    stable += 1
                    if stable >= 30:
                        return True
                elapsed += 1
        except FrameLimitReached:
            pass
        return False

    # Main battle menu navigation (2x2 grid)

    def _navigate_main_menu(self, target: int) -> None:
        """
        Navigate the main battle menu to *target* (0-indexed, 0=FIGHT, 1=PKMN, 2=ITEM, 3=RUN).
        """

        self._validate_index(target, 4, "Battle menu")
        for _ in range(5):
            if self.menu_state() != "main":
                raise RuntimeError("Expected main battle menu")
            row, column = self.menu_cursor(), self._r(0xCC25)
            if row not in (0, 1) or column not in (9, 15):
                raise RuntimeError("Invalid main-menu cursor")
            target_row, target_column = target // 2, 9 if target % 2 == 0 else 15
            if column != target_column:
                button = (
                    WindowEvent.PRESS_ARROW_RIGHT
                    if target_column == 15
                    else WindowEvent.PRESS_ARROW_LEFT
                )
            elif row != target_row:
                button = WindowEvent.PRESS_ARROW_DOWN if target_row else WindowEvent.PRESS_ARROW_UP
            else:
                return
            self._press(button)
        raise RuntimeError("Main-menu cursor did not reach requested choice")

    # Move sub-menu navigation (vertical list, 0-3)

    def _navigate_move_menu(self, target: int) -> None:
        """
        Select an existing move using the observed cursor, with retries bounded.
        """
        move_count = self._r(0xCC28) - 1

        if not 1 <= move_count <= 4:
            raise RuntimeError(f"Invalid move count {move_count}")

        if type(target) is not int or not 0 <= target < move_count:
            raise ValueError(f"Move index must be between 0 and {move_count - 1}, got {target}")

        for _ in range(4):
            current = self.move_cursor()
            if not 0 <= current < move_count:
                raise RuntimeError("Invalid move cursor")
            if current == target:
                return
            button = (
                WindowEvent.PRESS_ARROW_DOWN if target > current else WindowEvent.PRESS_ARROW_UP
            )
            self._press(button)

        if self.move_cursor() != target:
            raise RuntimeError("Move-menu cursor did not reach the requested move")

    # Back to main menu

    def _back_to_main_menu(self) -> bool:
        """
        Ensure the main battle menu is showing.

        If already there, returns immediately.  Otherwise calls wait_for_turn()
        which will advance any remaining text and return when menu is ready.
        """
        if self.menu_state() == "main":
            return True
        return (
            self.cancel()
            if self.menu_state() in ("moves", "bag", "party", "party_actions", "item_moves")
            else self.wait_for_turn() and self.menu_state() == "main"
        )

    def cancel(self) -> bool:
        """Back out of optional menus. A required replacement cannot be cancelled."""
        for _ in range(5):
            if self.needs_switch():
                return False
            state = self.menu_state()
            if state == "main":
                self.last_result = "cancelled"
                return True
            if state not in ("moves", "bag", "party", "party_actions", "item_moves"):
                return False
            self._press_b()
            self._tick(self.SUBMENU_SETTLE)
        return False

    # Public battle actions

    def fight(self, move_index: int) -> bool:
        """
        Select FIGHT and use move at *move_index* (0-indexed, 0=first move).

        Returns True if the inputs were sent successfully.
        Call wait_for_turn() afterwards to block until the next player turn.
        """
        self.last_result = None
        self._validate_index(move_index, 4, "Move")
        if not self.is_in_battle():
            print("[BATTLE] fight(): not in battle.")
            return False

        # Validate against the active battle moves (also correct after Transform)
        # before opening any menu. The menu count is checked again once drawn.
        move_count = sum(self._r(0xD01C + index) != 0 for index in range(4))
        self._validate_index(move_index, move_count, "Move")

        if not self._back_to_main_menu():
            print("[BATTLE] fight(): could not reach main battle menu.")
            return False

        # Navigate to FIGHT (top-left = 0)
        self._navigate_main_menu(FIGHT)
        self._press_a()
        if not self._wait_menu("moves"):
            return False

        # Navigate move list
        self._navigate_move_menu(move_index)
        self._press_a()
        print(f"[BATTLE] fight(move_index={move_index}) sent.")
        return True

    def switch(self, party_index: int) -> bool:
        """
        Select PKMN and switch to *party_index* (0-indexed party slot).

        Returns True once the active slot changes. Call wait_for_turn() afterwards.
        """
        self.last_result = None
        self._validate_index(party_index, self._r(0xD163), "Party")
        if party_index not in self.available_switches():
            raise ValueError("Switch target must be a healthy reserve Pokémon")
        if not self.is_in_battle():
            return False
        forced = self.needs_switch()
        if self.menu_state() == "next_pokemon":
            self._navigate_list(0, 2, "next_pokemon")
            self._press_a()
        elif not forced:
            if not self._back_to_main_menu():
                return False
            self._navigate_main_menu(PKMN)
            self._press_a()
        if not self._wait_menu("party"):
            return False
        self._navigate_list(party_index, self._r(0xD163), "party")
        self._press_a()
        if not forced:
            if not self._wait_menu("party_actions"):
                return False
            self._navigate_list(0, 3, "party_actions")  # SWITCH, STATS, CANCEL
            self._press_a()
        # Confirm the engine changed the active slot, not merely that A was sent.
        for elapsed in range(600):
            if self.active_mon_slot() == party_index:
                self.last_result = "switched"
                return True
            if elapsed % _TEXT_ADVANCE_EVERY == 0 and self.menu_state() == "text":
                self._press_b()
            else:
                self._tick()
        self.last_result = "timeout"
        return False

    def use_item(
        self, bag_index: int, party_index: int | None = None, move_index: int | None = None
    ) -> bool:
        """
        Select ITEM and use the item at *bag_index* (0-indexed bag slot).

        Medicine requires party_index; Ether additionally requires move_index.
        Returns True only after a consumable was spent. Inspect last_result to
        distinguish a caught Pokémon from a failed throw. Unsupported items are
        rejected before inputs; cancellation and no-effect uses return False.
        """
        self.last_result = None
        count = self._r(0xD31D)
        if count > 20:
            raise RuntimeError("Invalid bag size")
        self._validate_index(bag_index, count, "Bag")
        item = self._r(0xD31E + 2 * bag_index)
        balls = {1, 2, 3, 4}
        medicine = set(range(0x0B, 0x15)) | {0x34, 0x35, 0x36, 0x3C, 0x3D, 0x3E}
        restorers = {0x50, 0x51, 0x52, 0x53}
        battle_items = {0x2E, 0x33, 0x37, 0x3A, 0x41, 0x42, 0x43, 0x44}
        if item not in balls | medicine | restorers | battle_items:
            raise ValueError(f"Item 0x{item:02X} is not supported in battle")
        if item in medicine | restorers:
            self._validate_index(party_index, self._r(0xD163), "Party")
        elif party_index is not None:
            raise ValueError("This item does not take a party target")
        if item in {0x50, 0x51}:
            moves = sum(self._r(0xD173 + party_index * 44 + i) != 0 for i in range(4))
            self._validate_index(move_index, moves, "Move")
        elif move_index is not None:
            raise ValueError("This item does not take a move target")
        if not self.is_in_battle():
            return False
        if item in balls | {0x33} and not self.is_wild_battle():
            raise ValueError("Balls and Poké Dolls require a wild battle")
        if self._r(_BATTLE_TYPE) != 0:
            raise ValueError("Only normal battles support bag actions")
        before = self._item_quantity(item)
        if not before:
            raise ValueError("Selected item has no quantity")
        if not self._back_to_main_menu():
            return False
        self._navigate_main_menu(ITEM)
        self._press_a()
        if not self._wait_menu("bag"):
            return False
        self._navigate_list(bag_index, count, "bag", scroll=True)
        self._press_a()
        if party_index is not None:
            if not self._wait_menu("party"):
                return False
            self._navigate_list(party_index, self._r(0xD163), "party")
            self._press_a()
        if move_index is not None:
            # The technique question is dialogue before the move list appears.
            for _ in range(60):
                if self.menu_state() == "item_moves":
                    break
                if self.menu_state() == "text":
                    self._press_b()
                else:
                    self._tick()
            if not self._wait_menu("item_moves"):
                return False
            self._navigate_move_menu(move_index)
            self._press_a()
        return self._finish_item(item, before, is_ball=item in balls)

    def _item_quantity(self, item: int) -> int:
        return sum(
            self._r(0xD31F + 2 * i)
            for i in range(min(self._r(0xD31D), 20))
            if self._r(0xD31E + 2 * i) == item
        )

    def _finish_item(self, item: int, before: int, *, is_ball: bool) -> bool:
        budget = FrameBudget(self.pyboy, 6000)
        caught = False
        elapsed = 0
        try:
            while budget.remaining:
                budget.tick()
                text = " ".join(self.gs.dialog.upper().split())
                caught |= "WAS CAUGHT" in text
                state = self.menu_state()
                spent = self._item_quantity(item) < before
                if state in ("main", "ended", "next_pokemon") or self.needs_switch():
                    self.last_result = (
                        ("caught" if caught else "not_caught")
                        if is_ball and spent
                        else ("used" if spent else "rejected")
                    )
                    return spent
                if state == "bag":
                    self.cancel()
                    self.last_result = "rejected"
                    return False
                if state in ("party", "party_actions", "item_moves", "yes_no"):
                    self.last_result = "rejected"
                    return False
                if elapsed % _TEXT_ADVANCE_EVERY == 0:
                    budget.press(
                        WindowEvent.PRESS_BUTTON_B,
                        WindowEvent.RELEASE_BUTTON_B,
                        self.PRESS_FRAMES,
                        self.SETTLE_FRAMES,
                    )
                elapsed += 1
        except FrameLimitReached:
            pass
        self.last_result = "timeout"
        return False

    def run(self) -> bool:
        """
        Select RUN.  Returns True if inputs were sent.

        The actual escape is not guaranteed (low speed Pokémon may fail to flee
        from faster wild Pokémon).  Check is_in_battle() after wait_for_turn()
        to verify whether you escaped.
        """
        self.last_result = None
        if not self.is_in_battle():
            print("[BATTLE] run(): not in battle.")
            return False

        if not self._back_to_main_menu():
            print("[BATTLE] run(): could not reach main battle menu.")
            return False

        self._navigate_main_menu(RUN)
        self._press_a()
        print("[BATTLE] run() sent.")
        return True
