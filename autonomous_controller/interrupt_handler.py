"""
autonomous_controller/interrupt_handler.py

Detects and resolves NPC dialogue / textbox interrupts that occur during
autonomous navigation.  Also detects battle starts.

Detection strategy
------------------
`PokemonGameState.dialog` scans the VRAM tilemap buffer and returns the
visible text as a string.  A non-empty value means a textbox is active.
`PokemonGameState.map["in_battle"]` reads 0xD057 to detect battles.

Handling
--------
- Dialogue (not a battle): spam the A button until the text is gone.
- Battle starts: raise BattleInterrupt so the navigation call-stack can
  unwind cleanly.  A future AI-agent layer will re-enter via battle_action().

Usage
-----
    handler = InterruptHandler(pyboy, game_state)
    # inside _step():
    handler.check_and_handle()   # raises BattleInterrupt or returns normally
"""

from pyboy.utils import WindowEvent  # pylint: disable=no-name-in-module


class BattleInterrupt(Exception):
    """
    Raised when a battle starts during autonomous navigation.

    The navigation call-stack should unwind to go_to() which catches this and
    returns False.  A separate AI-battle-agent can then take over.
    """


class InterruptHandler:
    """
    Checks for textbox / battle interrupts and handles them.

    Attributes
    ----------
    MAX_A_PRESSES : int
        Safety cap — stop after this many A presses even if text is still showing.
        Prevents infinite loops on unskippable cutscenes.
    PRESS_FRAMES : int
        Frames to hold A button per press.
    SETTLE_FRAMES : int
        Frames to wait between A presses so the game can update the textbox state.
    """

    MAX_A_PRESSES = 120
    PRESS_FRAMES  = 2
    SETTLE_FRAMES = 10

    def __init__(self, pyboy, game_state):
        self.pyboy = pyboy
        self.gs    = game_state

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def is_dialog_active(self) -> bool:
        """True when any text is visible in the VRAM tilemap buffer."""
        return bool(self.gs.dialog.strip())

    def is_in_battle(self) -> bool:
        """True when the battle flag (0xD057) is non-zero."""
        return bool(self.gs.map["in_battle"])

    def is_interrupted(self) -> bool:
        """True when the game is showing a textbox but NOT in a battle.
        (Battle intros briefly show text before setting in_battle.)"""
        return self.is_dialog_active() or self.is_in_battle()

    # ------------------------------------------------------------------
    # Interrupt resolution
    # ------------------------------------------------------------------

    def _press_a(self) -> None:
        self.pyboy.send_input(WindowEvent.PRESS_BUTTON_A)
        for _ in range(self.PRESS_FRAMES):
            self.pyboy.tick()
        self.pyboy.send_input(WindowEvent.RELEASE_BUTTON_A)
        for _ in range(self.SETTLE_FRAMES):
            self.pyboy.tick()

    def check_and_handle(self) -> None:
        """
        Call this after every _step().

        If a battle has started ⟶ raises BattleInterrupt.
        If a textbox is open ⟶ spams A until it closes, then returns.
        If nothing is happening ⟶ returns immediately.

        Raises
        ------
        BattleInterrupt
            When the battle flag is set.
        """
        # Fast path — nothing happening
        if not self.is_interrupted():
            return

        # Battle takes priority — NPC dialogue can transition into a battle
        if self.is_in_battle():
            raise BattleInterrupt("Battle started during navigation")

        # --- Dialogue handling ---
        print("  [INT] Textbox detected — spamming A to advance dialogue…")
        for i in range(self.MAX_A_PRESSES):
            # Re-check battle every iteration (cutscene can start a battle)
            if self.is_in_battle():
                raise BattleInterrupt("Battle started during dialogue sequence")

            if not self.is_dialog_active():
                print(f"  [INT] Dialogue cleared after {i} A press(es)")
                return

            self._press_a()

        print(f"  [INT] Dialogue still active after {self.MAX_A_PRESSES} A presses — giving up")
