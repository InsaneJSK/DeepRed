"""
autonomous_controller/battle_controller.py

BattleController — RAM-driven battle menu navigation for Pokemon Red.

All menu state is read from RAM directly (no dialog scraping).

RAM addresses (verified against pret/pokered wram.asm + diagnostic):
    0xCC26  wCurrentMenuItem        current cursor in the active menu (0-indexed)
    0xCC29  wBattleAndStartSaved    main-menu cursor saved when sub-menu opens
    0xCC2A  wCurrentMoveNum         move slot selected in FIGHT sub-menu (0-indexed)
    0xCC2E  wPlayerMonNumber        active party Pokémon slot (0-indexed)
    0xCCD5  wBattleTurnSide         3=player's turn / choosing, 4=enemy turn / anim
    0xD013  wBattleResult           0=win, 1=lose, 2=draw (valid after battle ends)
    0xD057  wIsInBattle             0=none, 1=wild, 2=trainer
    0xD05A  wBattleType             0=normal, 1=old man, 2=safari

Main battle menu layout (2×2 grid):
    FIGHT(0) | PKMN(1)
    ----------+--------
    ITEM(2)  | RUN(3)

    Row  = index // 2   (0=top, 1=bottom)
    Col  = index  % 2   (0=left, 1=right)
    RIGHT ↔ toggles column, DOWN ↔ toggles row.

Usage (from AI agent):
    bc = BattleController(pyboy, gs)
    bc.fight(move_index=0)          # use first move in move list
    bc.wait_for_turn()              # block until next player turn
    bc.run()                        # try to run
    bc.switch(party_index=1)        # switch to second party slot
    bc.use_item(bag_index=0)        # use first item in bag
"""

from pyboy.utils import WindowEvent  # pylint: disable=no-name-in-module

# ---------------------------------------------------------------------------
# RAM addresses (verified against pret/pokered + live diagnostic)
# ---------------------------------------------------------------------------
_MENU_CURSOR      = 0xCC26  # wCurrentMenuItem  — cursor in current menu
_SAVED_CURSOR     = 0xCC29  # wBattleAndStartSavedMenuItem
_MOVE_CURSOR      = 0xCC2A  # wCurrentMoveNum   — move slot in FIGHT sub-menu
_PLAYER_MON_SLOT  = 0xCC2E  # wPlayerMonNumber  — active party slot (0-indexed)
_BATTLE_TURN_SIDE = 0xCCD5  # NOT reliable as a turn-indicator (varies by frame)
_IS_IN_BATTLE     = 0xD057  # wIsInBattle: 0=none, 1=wild, 2=trainer
_BATTLE_TYPE      = 0xD05A  # wBattleType: 0=normal, 1=old man, 2=safari

# Main battle menu option indices
FIGHT = 0
PKMN  = 1
ITEM  = 2
RUN   = 3

# Player-turn sentinel (wBattleTurnSide == 3 means battle menu is showing)
_PLAYER_TURN_VALUE  = 3
# How often (in ticks) to press A while waiting for the menu (advances text)
_TEXT_ADVANCE_EVERY = 20


class BattleController:
    """
    RAM-driven navigation of Pokemon Red battle menus.

    No dialog scraping — every state query reads a specific WRAM address.
    """

    # Ticks to hold a button press
    PRESS_FRAMES   = 3
    # Ticks to settle between presses
    SETTLE_FRAMES  = 8
    # Ticks to wait for the screen to settle after opening a sub-menu
    SUBMENU_SETTLE = 30
    # Max ticks for wait_for_turn() (~10 s at 60 fps — covers long animations)
    TURN_TIMEOUT   = 600

    def __init__(self, pyboy, game_state):
        self.pyboy = pyboy
        self.gs    = game_state

        self._release_map = {
            WindowEvent.PRESS_ARROW_UP:    WindowEvent.RELEASE_ARROW_UP,
            WindowEvent.PRESS_ARROW_DOWN:  WindowEvent.RELEASE_ARROW_DOWN,
            WindowEvent.PRESS_ARROW_LEFT:  WindowEvent.RELEASE_ARROW_LEFT,
            WindowEvent.PRESS_ARROW_RIGHT: WindowEvent.RELEASE_ARROW_RIGHT,
            WindowEvent.PRESS_BUTTON_A:    WindowEvent.RELEASE_BUTTON_A,
            WindowEvent.PRESS_BUTTON_B:    WindowEvent.RELEASE_BUTTON_B,
        }

    # ------------------------------------------------------------------
    # RAM helpers
    # ------------------------------------------------------------------

    def _r(self, addr: int) -> int:
        """Read one byte from WRAM."""
        return self.gs.mem.read_byte(addr)

    def _tick(self, n: int = 1) -> None:
        for _ in range(n):
            self.pyboy.tick()

    def _press(self, btn: WindowEvent) -> None:
        """Hold *btn* for PRESS_FRAMES ticks then release and settle."""
        self.pyboy.send_input(btn)
        self._tick(self.PRESS_FRAMES)
        self.pyboy.send_input(self._release_map[btn])
        self._tick(self.SETTLE_FRAMES)

    def _press_a(self) -> None:
        self._press(WindowEvent.PRESS_BUTTON_A)

    def _press_b(self) -> None:
        self._press(WindowEvent.PRESS_BUTTON_B)

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def is_in_battle(self) -> bool:
        """True while the battle flag is set."""
        return self._r(_IS_IN_BATTLE) != 0

    def is_wild_battle(self) -> bool:
        """True when fighting a wild Pokémon (wIsInBattle == 1)."""
        return self._r(_IS_IN_BATTLE) == 1

    def is_player_turn(self) -> bool:
        """
        True when the main FIGHT/PKMN/ITEM/RUN battle menu is visible.

        Detected via dialog (VRAM tilemap): both "FIGHT" and "RUN" appear
        on-screen only when the 2×2 main battle menu is drawn.  They are
        absent during text boxes, move selection, party screen, and bag.

        (wBattleTurnSide / 0xCCD5 turned out to not reliably indicate
        player-turn state across different battle encounters.)
        """
        if self._r(_IS_IN_BATTLE) == 0:
            return False
        d = self.gs.dialog.upper()
        return "FIGHT" in d and "RUN" in d

    def menu_cursor(self) -> int:
        """Current cursor position in the active menu (0-indexed)."""
        return self._r(_MENU_CURSOR)

    def move_cursor(self) -> int:
        """Current move slot cursor in the FIGHT sub-menu (0-indexed)."""
        return self._r(_MOVE_CURSOR)

    def active_mon_slot(self) -> int:
        """Index of the player's currently active Pokémon (0-indexed)."""
        return self._r(_PLAYER_MON_SLOT)

    # ------------------------------------------------------------------
    # Waiting
    # ------------------------------------------------------------------

    def wait_for_turn(self, timeout: int | None = None) -> bool:
        """
        Block until the player's MAIN battle menu is ready.

        Returns True  — main battle menu is visible.
        Returns False — battle ended (wIsInBattle == 0) OR timed out.

        Callers that need to distinguish the two cases should check
        is_in_battle() after receiving False.

        While not at the menu, presses B every _TEXT_ADVANCE_EVERY ticks
        to advance text.  B is safe at all battle states (never selects menus).
        """
        limit = timeout if timeout is not None else self.TURN_TIMEOUT

        for tick in range(limit):
            self._tick()

            # Early exit: battle ended — caller should clear remaining text
            if not self.is_in_battle():
                return False

            if self.is_player_turn():
                self._tick(self.SUBMENU_SETTLE)
                if self.is_player_turn():
                    return True

            else:
                if tick % _TEXT_ADVANCE_EVERY == 0:
                    self.pyboy.send_input(WindowEvent.PRESS_BUTTON_B)
                    self._tick(self.PRESS_FRAMES)
                    self.pyboy.send_input(WindowEvent.RELEASE_BUTTON_B)
                    self._tick(self.SETTLE_FRAMES)

        return False

    def clear_post_battle_text(self, timeout: int = 3000) -> None:
        """
        Advance any remaining text after a battle ends (XP gain, level-up, etc.).

        Presses B until the dialog buffer has been empty for 30 consecutive
        ticks (overworld fully resumed) or the timeout is reached.
        """
        stable = 0
        STABLE_NEEDED = 30

        for tick in range(timeout):
            self._tick()

            if self.gs.dialog.strip():
                stable = 0
                if tick % _TEXT_ADVANCE_EVERY == 0:
                    self.pyboy.send_input(WindowEvent.PRESS_BUTTON_B)
                    self._tick(self.PRESS_FRAMES)
                    self.pyboy.send_input(WindowEvent.RELEASE_BUTTON_B)
                    self._tick(self.SETTLE_FRAMES)
            else:
                stable += 1
                if stable >= STABLE_NEEDED:
                    break

    # ------------------------------------------------------------------
    # Main battle menu navigation (2×2 grid)
    # ------------------------------------------------------------------

    def _navigate_main_menu(self, target: int) -> None:
        """
        Move the cursor to *target* (0=FIGHT, 1=PKMN, 2=ITEM, 3=RUN).

        Layout:
            FIGHT(0) | PKMN(1)
            ITEM(2)  | RUN(3)

        Always resets to FIGHT (top-left) first with UP+LEFT, then navigates
        to the target.  This avoids the remembered-cursor problem.
        """
        # Reset to FIGHT (top-left corner) — UP and LEFT each wrap within
        # their axis, so 1 press each is enough for a 2×2 grid.
        self._press(WindowEvent.PRESS_ARROW_UP)
        self._press(WindowEvent.PRESS_ARROW_LEFT)

        if target == FIGHT:
            return

        tgt_row, tgt_col = target // 2, target % 2
        if tgt_col == 1:
            self._press(WindowEvent.PRESS_ARROW_RIGHT)
        if tgt_row == 1:
            self._press(WindowEvent.PRESS_ARROW_DOWN)

    # ------------------------------------------------------------------
    # Move sub-menu navigation (vertical list, 0-3)
    # ------------------------------------------------------------------

    def _navigate_move_menu(self, target: int) -> None:
        """
        Navigate the FIGHT move list to *target* (0-indexed from the top).

        Gen 1 remembers the last-used move cursor, so the sub-menu can open
        at any position.  We always reset to the top of the list first (3×UP
        covers any 4-move list), then press DOWN *target* times.
        """
        # Reset to top
        for _ in range(3):
            self._press(WindowEvent.PRESS_ARROW_UP)
        # Navigate down to target
        for _ in range(target):
            self._press(WindowEvent.PRESS_ARROW_DOWN)

    # ------------------------------------------------------------------
    # Back to main menu
    # ------------------------------------------------------------------

    def _back_to_main_menu(self) -> bool:
        """
        Ensure the main battle menu is showing.

        If already there, returns immediately.  Otherwise calls wait_for_turn()
        which will advance any remaining text and return when menu is ready.
        """
        if self.is_player_turn():
            return True
        return self.wait_for_turn()



    # ------------------------------------------------------------------
    # Public battle actions
    # ------------------------------------------------------------------

    def fight(self, move_index: int) -> bool:
        """
        Select FIGHT and use move at *move_index* (0-indexed, 0=first move).

        Returns True if the inputs were sent successfully.
        Call wait_for_turn() afterwards to block until the next player turn.
        """
        if not self.is_in_battle():
            print("[BATTLE] fight(): not in battle.")
            return False

        if not self._back_to_main_menu():
            print("[BATTLE] fight(): could not reach main battle menu.")
            return False

        # Navigate to FIGHT (top-left = 0)
        self._navigate_main_menu(FIGHT)
        self._press_a()
        self._tick(self.SUBMENU_SETTLE)

        # Navigate move list
        self._navigate_move_menu(move_index)
        self._press_a()
        print(f"[BATTLE] fight(move_index={move_index}) sent.")
        return True

    def switch(self, party_index: int) -> bool:
        """
        Select PKMN and switch to *party_index* (0-indexed party slot).

        Returns True if inputs were sent.  Call wait_for_turn() afterwards.
        """
        if not self.is_in_battle():
            print("[BATTLE] switch(): not in battle.")
            return False

        if not self._back_to_main_menu():
            print("[BATTLE] switch(): could not reach main battle menu.")
            return False

        self._navigate_main_menu(PKMN)
        self._press_a()
        self._tick(self.SUBMENU_SETTLE)

        # Navigate vertical party list to party_index
        for _attempt in range(4):
            current = self.menu_cursor()
            if current == party_index:
                break
            diff = party_index - current
            btn  = WindowEvent.PRESS_ARROW_DOWN if diff > 0 else WindowEvent.PRESS_ARROW_UP
            for _ in range(abs(diff)):
                self._press(btn)

        self._press_a()   # open Pokémon sub-menu (SHIFT / STATS / CANCEL)
        self._tick(self.SUBMENU_SETTLE)

        # SHIFT is the first option — navigate up to ensure cursor is there
        self._press(WindowEvent.PRESS_ARROW_UP)
        self._press_a()
        print(f"[BATTLE] switch(party_index={party_index}) sent.")
        return True

    def use_item(self, bag_index: int) -> bool:
        """
        Select ITEM and use the item at *bag_index* (0-indexed bag slot).

        Returns True if inputs were sent.  Call wait_for_turn() afterwards.
        """
        if not self.is_in_battle():
            print("[BATTLE] use_item(): not in battle.")
            return False

        if not self._back_to_main_menu():
            print("[BATTLE] use_item(): could not reach main battle menu.")
            return False

        self._navigate_main_menu(ITEM)
        self._press_a()
        self._tick(self.SUBMENU_SETTLE)

        # Navigate vertical bag list to bag_index
        for _attempt in range(bag_index + 4):   # +4 guard iterations
            current = self.menu_cursor()
            if current == bag_index:
                break
            diff = bag_index - current
            btn  = WindowEvent.PRESS_ARROW_DOWN if diff > 0 else WindowEvent.PRESS_ARROW_UP
            for _ in range(min(abs(diff), 1)):    # one step at a time (avoids overscroll)
                self._press(btn)

        self._press_a()
        print(f"[BATTLE] use_item(bag_index={bag_index}) sent.")
        return True

    def run(self) -> bool:
        """
        Select RUN.  Returns True if inputs were sent.

        The actual escape is not guaranteed (low speed Pokémon may fail to flee
        from faster wild Pokémon).  Check is_in_battle() after wait_for_turn()
        to verify whether you escaped.
        """
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
