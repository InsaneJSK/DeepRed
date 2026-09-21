"""Handle navigation dialogue within a strict emulated-frame budget."""

from pyboy.utils import WindowEvent

from autonomous_controller.emulator_session import FrameBudget, FrameLimitReached


class BattleInterrupt(Exception):
    """A battle started; the caller must handle combat and resume."""


class StarterChoiceRequired(Exception):
    """Navigation reached Oak's starter decision; the caller must choose."""


class ControlTimeout(TimeoutError):
    """Dialogue or scripted movement did not release control in time."""


class InterruptHandler:
    PRESS_FRAMES = 2
    SETTLE_FRAMES = 10
    CONTROL_GRACE = 120
    CONTROL_TIMEOUT = 18000

    def __init__(self, pyboy, game_state):
        self.pyboy = pyboy
        self.gs = game_state
        self.was_displaced = False

    def _is_dialog_active(self):
        return bool(self.gs.dialog.strip())

    def _is_in_battle(self):
        return bool(self.gs.map["in_battle"])

    def is_interrupted(self):
        return self._is_in_battle() or self._is_dialog_active()

    def _current_pos(self):
        m = self.gs.map
        return m["player_x"], m["player_y"]

    def wait_for_control(self, grace_frames=None, timeout_frames=None):
        """Return False on timeout; nested input frames count toward the limit."""
        grace = self.CONTROL_GRACE if grace_frames is None else grace_frames
        timeout = self.CONTROL_TIMEOUT if timeout_frames is None else timeout_frames
        budget = FrameBudget(self.pyboy, timeout)
        clear, last_pos = 0, None
        try:
            while budget.remaining:
                budget.tick()
                if self._is_in_battle():
                    raise BattleInterrupt("Battle during wait_for_control")
                if self._is_dialog_active():
                    button = self.dialogue_button()
                    release = (
                        WindowEvent.RELEASE_BUTTON_B
                        if button == WindowEvent.PRESS_BUTTON_B
                        else WindowEvent.RELEASE_BUTTON_A
                    )
                    budget.press(
                        button,
                        release,
                        self.PRESS_FRAMES,
                        self.SETTLE_FRAMES,
                    )
                    clear, last_pos = 0, None
                    continue
                pos = self._current_pos()
                clear = clear + 1 if pos == last_pos else 0
                last_pos = pos
                if clear >= grace:
                    return True
        except FrameLimitReached:
            pass
        return False

    def check_and_handle(self):
        self.was_displaced = False
        if self._is_in_battle():
            raise BattleInterrupt("Battle started during navigation")
        if not self._is_dialog_active():
            return
        before = self._current_pos()
        if not self.wait_for_control():
            raise ControlTimeout("Timed out waiting for dialogue/scripted movement to finish")
        self.was_displaced = self._current_pos() != before

    def dialogue_button(self):
        """Decline the nickname question; advance ordinary dialogue with A."""
        text = " ".join(self.gs.dialog.upper().split())

        if "GIVE A NICKNAME TO" in text and "?" in text and "YES" in text and "NO" in text:
            return WindowEvent.PRESS_BUTTON_B

        return WindowEvent.PRESS_BUTTON_A
