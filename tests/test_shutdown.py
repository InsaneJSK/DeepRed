"""Window-close cancellation must escape nested loops and release the emulator."""

import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from pyboy import PyBoy
from pyboy.utils import WindowEvent

import main
from autonomous_controller.battle_controller import BattleController
from autonomous_controller.emulator_session import EmulatorClosed, EmulatorSession
from autonomous_controller.nav_core import NavCore


@pytest.fixture(autouse=True)
def isolate_lifecycle_from_rom_validation(monkeypatch):
    monkeypatch.setattr(main, "validate_rom", lambda path: None)


class ShutdownTests(unittest.TestCase):
    def test_session_forwards_access_and_tick_arguments(self):
        emulator = Mock()
        emulator.tick.return_value = True
        session = EmulatorSession(emulator)
        self.assertIs(session.memory, emulator.memory)
        self.assertTrue(session.tick(20, render=False))
        emulator.tick.assert_called_once_with(20, render=False)

    def test_quit_interrupts_movement_and_releases_button(self):
        emulator = Mock()
        emulator.tick.return_value = False
        core = NavCore()
        core.pyboy = EmulatorSession(emulator)
        core._release_map = {WindowEvent.PRESS_BUTTON_A: WindowEvent.RELEASE_BUTTON_A}
        with self.assertRaises(EmulatorClosed):
            core.press(WindowEvent.PRESS_BUTTON_A)
        emulator.send_input.assert_called_with(WindowEvent.RELEASE_BUTTON_A)
        self.assertEqual(emulator.tick.call_count, 1)

    def test_quit_interrupts_battle_wait_without_running_timeout(self):
        emulator = Mock()
        emulator.tick.return_value = False
        battle = BattleController(EmulatorSession(emulator), Mock())
        with self.assertRaises(EmulatorClosed):
            battle.wait_for_turn(timeout=6000)
        self.assertEqual(emulator.tick.call_count, 1)

    def test_main_cleans_up_on_window_close_ctrl_c_and_errors(self):
        for error in (EmulatorClosed(), KeyboardInterrupt(), RuntimeError("failure")):
            with self.subTest(error=type(error).__name__):
                emulator = Mock()
                with (
                    patch.object(main, "PyBoy", return_value=emulator),
                    patch.object(main, "_run_agent", side_effect=error),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    if isinstance(error, RuntimeError):
                        with self.assertRaises(RuntimeError):
                            main.main()
                    else:
                        main.main()
                emulator.stop.assert_called_once_with(save=False)

    def test_idle_window_close_returns_from_main(self):
        emulator = Mock()
        emulator.tick.side_effect = [True, True, False]
        with (
            patch.object(main, "PyBoy", return_value=emulator),
            patch("builtins.open", unittest.mock.mock_open(read_data=b"")),
            patch.object(main, "PokemonGameState"),
            patch.object(main, "AutonomousController") as controller,
            patch.object(main, "BattleController") as battle,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            controller.return_value.go_to.return_value = True
            battle.return_value.is_in_battle.return_value = False
            main.main()
        self.assertEqual(emulator.tick.call_count, 3)
        emulator.stop.assert_called_once_with(save=False)

    @pytest.mark.integration
    def test_real_pyboy_quit_event(self):
        rom = Path(__file__).resolve().parents[1] / "Pokemon_Red/Red.gb"
        if not rom.exists():
            self.skipTest("Local ROM required for the real quit-event check")
        emulator = PyBoy(str(rom), window="null", sound_emulated=False)
        try:
            emulator.set_emulation_speed(0)
            session = EmulatorSession(emulator)
            session.tick()
            emulator.send_input(WindowEvent.QUIT)
            with self.assertRaises(EmulatorClosed):
                session.tick()
        finally:
            emulator.stop(save=False)


if __name__ == "__main__":
    unittest.main()
