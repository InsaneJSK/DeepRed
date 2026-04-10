"""
autonomous_controller/battle_controller.py

BattleController --- handles autonomous menu navigation during battles.
"""

from typing import Optional
from pyboy.utils import WindowEvent  # pylint: disable=no-name-in-module
from autonomous_controller.nav_core import NavCore

class BattleController(NavCore):
    """
    High-level agent to navigate Battle menus using `gs.dialog`.
    Requires the game state to ensure `in_battle` is true.
    """

    def __init__(self, pyboy, game_state):
        super().__init__()
        self.pyboy = pyboy
        self.gs = game_state
        self._release_map = {
            WindowEvent.PRESS_ARROW_UP:     WindowEvent.RELEASE_ARROW_UP,
            WindowEvent.PRESS_ARROW_DOWN:   WindowEvent.RELEASE_ARROW_DOWN,
            WindowEvent.PRESS_ARROW_LEFT:   WindowEvent.RELEASE_ARROW_LEFT,
            WindowEvent.PRESS_ARROW_RIGHT:  WindowEvent.RELEASE_ARROW_RIGHT,
            WindowEvent.PRESS_BUTTON_A:     WindowEvent.RELEASE_BUTTON_A,
            WindowEvent.PRESS_BUTTON_B:     WindowEvent.RELEASE_BUTTON_B,
            WindowEvent.PRESS_BUTTON_START: WindowEvent.RELEASE_BUTTON_START,
        }

    def _wait_for_menu(self, expected_words: list[str], max_ticks=300) -> bool:
        """Wait until specific words appear in the dialog, indicating the menu is ready."""
        for _ in range(max_ticks):
            dialog = self.gs.dialog.upper()
            if all(word in dialog for word in expected_words):
                return True
            self.pyboy.tick()
        return False

    def _back_to_main_menu(self) -> bool:
        """Press B until the main menu (FIGHT, PKMN, ITEM, RUN) shows up."""
        for _ in range(10): # try up to 10 B presses
            if self._wait_for_menu(["FIGHT", "PKMN"], max_ticks=30):
                return True
            self.press(WindowEvent.PRESS_BUTTON_B, frames=2)
            for _ in range(20):
                self.pyboy.tick()
        return self._wait_for_menu(["FIGHT", "PKMN"], max_ticks=30)

    def _find_cursor_index_and_target_index(self, dialog_lines: list[str], target_name: str, 
                                            ignore_empty=True) -> tuple[int, int]:
        """
        Parses dialog lines. Finds the 0-indexed row of '►' and the 0-indexed row of `target_name`.
        Returns (cursor_index, target_index). Returns (-1, -1) if unable to find target.
        """
        # Filter out empty lines if needed
        if ignore_empty:
            dialog_lines = [line for line in dialog_lines if line.strip()]

        cursor_index = -1
        target_index = -1

        for i, line in enumerate(dialog_lines):
            line_upper = line.upper()
            if '►' in line_upper:
                cursor_index = i
            if target_name.upper() in line_upper:
                target_index = i
                
        return cursor_index, target_index

    def _navigate_list_vertical(self, current_idx: int, target_idx: int):
        """Press Up/Down to move the cursor to the target index."""
        if current_idx == -1 or target_idx == -1:
            return

        diff = target_idx - current_idx
        if diff > 0:
            for _ in range(diff):
                self.press(WindowEvent.PRESS_ARROW_DOWN, frames=2)
                for _ in range(10): self.pyboy.tick()
        elif diff < 0:
            for _ in range(abs(diff)):
                self.press(WindowEvent.PRESS_ARROW_UP, frames=2)
                for _ in range(10): self.pyboy.tick()

    def fight(self, move_name: str) -> bool:
        """
        Select 'FIGHT', choose `move_name`, and execute it.
        Returns True if successful, False if move wasn't found or an error occurred.
        """
        if not self.gs.map.get("in_battle"):
            print("[BATTLE] Not in battle.")
            return False

        if not self._back_to_main_menu():
            print("[BATTLE] Could not reach main battle menu.")
            return False

        # In main menu, 'FIGHT' is top-left.
        # Press UP/LEFT to assure cursor is on FIGHT.
        self.press(WindowEvent.PRESS_ARROW_UP, frames=2)
        self.press(WindowEvent.PRESS_ARROW_LEFT, frames=2)
        for _ in range(10): self.pyboy.tick()

        # Press A to select FIGHT
        self.press(WindowEvent.PRESS_BUTTON_A, frames=2)
        
        # Wait for moves to appear. A move menu shouldn't contain "PKMN" or "RUN"
        # We wait for the screen to settle.
        for _ in range(30): self.pyboy.tick()

        # Try to find the move in the dialog list
        dialog_lines = self.gs.dialog.strip().split('\n')
        cursor_idx, target_idx = self._find_cursor_index_and_target_index(dialog_lines, move_name)

        if target_idx == -1:
            print(f"[BATTLE] Move '{move_name}' not found in menu.")
            return False

        self._navigate_list_vertical(cursor_idx, target_idx)
        
        # Press A to use the move
        self.press(WindowEvent.PRESS_BUTTON_A, frames=2)
        
        # Wait for the action to start (dialog should clear or change)
        for _ in range(30): self.pyboy.tick()
        print(f"[BATTLE] Executed fight({move_name}).")
        return True

    def pkmn(self, pokemon_name: str) -> bool:
        """
        Select 'PKMN', choose `pokemon_name`, and bring it out.
        """
        if not self._back_to_main_menu():
            return False

        # Move to PKMN: from FIGHT (top-left), press RIGHT
        self.press(WindowEvent.PRESS_ARROW_UP, frames=2)
        self.press(WindowEvent.PRESS_ARROW_LEFT, frames=2)
        self.press(WindowEvent.PRESS_ARROW_RIGHT, frames=2)
        for _ in range(10): self.pyboy.tick()

        self.press(WindowEvent.PRESS_BUTTON_A, frames=2)
        
        # Wait for party list
        for _ in range(45): self.pyboy.tick()

        dialog_lines = self.gs.dialog.strip().split('\n')
        cursor_idx, target_idx = self._find_cursor_index_and_target_index(dialog_lines, pokemon_name)

        if target_idx == -1:
            print(f"[BATTLE] Pokemon '{pokemon_name}' not found.")
            return False

        self._navigate_list_vertical(cursor_idx, target_idx)

        # Press A on Pokemon
        self.press(WindowEvent.PRESS_BUTTON_A, frames=2)
        for _ in range(30): self.pyboy.tick()
        
        # In the sub-menu (SHIFT/SEND OUT, STATS, CANCEL), Shift/Send Out is at the top
        self.press(WindowEvent.PRESS_ARROW_UP, frames=2)
        self.press(WindowEvent.PRESS_BUTTON_A, frames=2)
        
        print(f"[BATTLE] Selected pkmn({pokemon_name}).")
        return True

    def item(self, item_name: str, pocket: Optional[str] = None) -> bool:
        """
        Select 'ITEM', find `item_name`, and use it.
        Bag in Gen 1 is a scrolling list. 
        """
        if not self._back_to_main_menu():
            return False

        # Move to ITEM: go to FIGHT (top-left) -> DOWN
        self.press(WindowEvent.PRESS_ARROW_UP, frames=2)
        self.press(WindowEvent.PRESS_ARROW_LEFT, frames=2)
        self.press(WindowEvent.PRESS_ARROW_DOWN, frames=2)
        for _ in range(10): self.pyboy.tick()

        self.press(WindowEvent.PRESS_BUTTON_A, frames=2)

        # Wait for bag
        for _ in range(45): self.pyboy.tick()

        # Scrolling list logic
        for _ in range(20): # max scroll attempts
            dialog_lines = self.gs.dialog.strip().split('\n')
            cursor_idx, target_idx = self._find_cursor_index_and_target_index(dialog_lines, item_name)
            
            if target_idx != -1:
                # Found it
                self._navigate_list_vertical(cursor_idx, target_idx)
                # Press A to use
                self.press(WindowEvent.PRESS_BUTTON_A, frames=2)
                for _ in range(30): self.pyboy.tick()
                print(f"[BATTLE] Used item({item_name}).")
                return True
                
            # Scroll down
            self.press(WindowEvent.PRESS_ARROW_DOWN, frames=2)
            for _ in range(10): self.pyboy.tick()

        print(f"[BATTLE] Item '{item_name}' not found.")
        return False

    def run(self) -> bool:
        """
        Select 'RUN'.
        """
        if not self._back_to_main_menu():
            return False

        # Move to RUN: FIGHT(TL) -> RIGHT -> DOWN
        self.press(WindowEvent.PRESS_ARROW_UP, frames=2)
        self.press(WindowEvent.PRESS_ARROW_LEFT, frames=2)
        self.press(WindowEvent.PRESS_ARROW_RIGHT, frames=2)
        self.press(WindowEvent.PRESS_ARROW_DOWN, frames=2)
        for _ in range(10): self.pyboy.tick()

        self.press(WindowEvent.PRESS_BUTTON_A, frames=2)
        for _ in range(30): self.pyboy.tick()
        
        print("[BATTLE] Attempted to run().")
        return True
