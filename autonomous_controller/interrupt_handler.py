"""
autonomous_controller/interrupt_handler.py
Detects NPC dialogue / battle interrupts during navigation.
After clearing dialogue, waits for player position to stabilise (NPC scripted
movement may continue for several frames after the last text box closes).
Sets `was_displaced` = True whenever an interrupt was handled so that
navigate_to_tile can skip the dodge pass and re-plan A* from the real position.
"""
from pyboy.utils import WindowEvent  # pylint: disable=no-name-in-module
class BattleInterrupt(Exception):
    """Raised when a battle starts during navigation.  go_to() catches this."""
class InterruptHandler:
    """
    Handles interrupts during navigation.
    """
    MAX_A_PRESSES      = 120   # safety cap on A spamming
    PRESS_FRAMES       = 2     # frames to hold A per press
    SETTLE_FRAMES      = 10    # frames between A presses
    STABILIZE_TICKS    = 5     # ticks between position polls after dialog
    STABILIZE_STABLE   = 8     # consecutive stable polls required
    STABILIZE_MAX      = 180   # max total polls (~15 s) before giving up
    CONTROL_GRACE      = 120   # tick frames of no-dialog required after stabilise
    CONTROL_TIMEOUT    = 18000 # max ticks to wait for control (~5 min at 60 fps)
    def __init__(self, pyboy, game_state):
        self.pyboy = pyboy
        self.gs    = game_state
        self.was_displaced: bool = False   # set True if NPC moved the player
    # State queries
    def _is_dialog_active(self) -> bool:
        return bool(self.gs.dialog.strip())
    def _is_in_battle(self) -> bool:
        return bool(self.gs.map["in_battle"])
    def is_interrupted(self) -> bool:
        """
        Returns True if the player is interrupted by dialogue or battle.
        """
        return self._is_dialog_active() or self._is_in_battle()
    # Internal helpers
    def _press_a(self) -> None:
        self.pyboy.send_input(WindowEvent.PRESS_BUTTON_A)
        for _ in range(self.PRESS_FRAMES):
            self.pyboy.tick()
        self.pyboy.send_input(WindowEvent.RELEASE_BUTTON_A)
        for _ in range(self.SETTLE_FRAMES):
            self.pyboy.tick()
    def _current_pos(self) -> tuple[int, int]:
        m = self.gs.map
        return m["player_x"], m["player_y"]
    def _stabilize(self) -> tuple[int, int]:
        """
        Tick until player position and dialog are both stable.
        Stable means: *STABILIZE_STABLE* consecutive polls where
        BOTH position unchanged AND no dialog is active.
        """
        stable_count = 0
        last_pos: tuple[int, int] | None = None
        for _ in range(self.STABILIZE_MAX):
            for _ in range(self.STABILIZE_TICKS):
                self.pyboy.tick()
            # Clear any dialog that appeared since the last poll
            if self._is_in_battle():
                raise BattleInterrupt("Battle during stabilisation")
            if self._is_dialog_active():
                print("  [INT] New dialogue mid-stabilisation — clearing…")
                for _ in range(self.MAX_A_PRESSES):
                    if self._is_in_battle():
                        raise BattleInterrupt("Battle started during dialogue")
                    if not self._is_dialog_active():
                        break
                    self._press_a()
                stable_count = 0
                last_pos = None
                continue
            pos = self._current_pos()
            if pos == last_pos:
                stable_count += 1
                if stable_count >= self.STABILIZE_STABLE:
                    return pos
            else:
                stable_count = 0
                last_pos = pos
        return self._current_pos()
    def wait_for_control(
        self,
        grace_frames: int | None = None,
        timeout_frames: int | None = None,
    ) -> bool:
        """
        Block until the player has regained full movement control.
        Strategy
        --------
        Loop until CONTROL_GRACE consecutive frames pass with:
          - No dialogue active
          - No battle
          - Position not changing
        Parameters:
            grace_frames : int
            timeout_frames : int
        Returns:
            True  — player has control.
            False — timed out.
        Raises:
            BattleInterrupt — if a battle starts while waiting.
        """
        grace   = grace_frames   if grace_frames   is not None else self.CONTROL_GRACE
        timeout = timeout_frames if timeout_frames is not None else self.CONTROL_TIMEOUT
        consecutive_clear = 0
        last_pos: tuple[int, int] | None = None
        print(f"  [INT] Waiting for player control (grace={grace} frames, timeout={timeout})…")
        for tick in range(timeout):
            self.pyboy.tick()
            if self._is_in_battle():
                raise BattleInterrupt("Battle during wait_for_control")
            if self._is_dialog_active():
                # Clear the dialogue
                for _ in range(self.MAX_A_PRESSES):
                    if self._is_in_battle():
                        raise BattleInterrupt("Battle during dialogue wait")
                    if not self._is_dialog_active():
                        break
                    self._press_a()
                consecutive_clear = 0
                last_pos = None
                continue
            pos = self._current_pos()
            if pos != last_pos:
                # Still moving (scripted walk)
                consecutive_clear = 0
                last_pos = pos
            else:
                consecutive_clear += 1
                if consecutive_clear >= grace:
                    print(f"  [INT] Player control confirmed after {tick + 1} ticks")
                    return True
        print(f"  [INT] wait_for_control timed out after {timeout} ticks")
        return False
    # Public entry point — called at the end of every _step()
    def check_and_handle(self) -> None:
        """
        Fast-path if nothing is happening.
        On dialogue: spam A, then wait for position to stabilise.
        On battle: raise BattleInterrupt.
        Sets self.was_displaced = True when the player ended up somewhere
        other than where they were before the interrupt began.
        """
        self.was_displaced = False
        if not self.is_interrupted():
            return
        if self._is_in_battle():
            raise BattleInterrupt("Battle started during navigation")
        pos_before = self._current_pos()
        print("  [INT] Textbox detected — spamming A to advance dialogue…")
        for i in range(self.MAX_A_PRESSES):
            if self._is_in_battle():
                raise BattleInterrupt("Battle started during dialogue sequence")
            if not self._is_dialog_active():
                print(f"  [INT] Dialogue cleared after {i} A press(es)")
                break
            self._press_a()
        else:
            print(f"  [INT] Dialogue still active after {self.MAX_A_PRESSES} presses — giving up")
        # Wait for any scripted NPC movement to finish
        print("  [INT] Stabilising player position…")
        pos_after = self._stabilize()
        if pos_after != pos_before:
            print(f"  [INT] Player displaced {pos_before} → {pos_after}")
            self.was_displaced = True
        else:
            print(f"  [INT] Position stable at {pos_after}")
