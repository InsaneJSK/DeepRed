"""Verify move consumption in a real battle, not just cursor positioning."""

from pathlib import Path

import pytest
from pyboy import PyBoy

from autonomous_controller import AutonomousController, BattleController, BattleInterrupt
from autonomous_controller.emulator_session import EmulatorSession
from autonomous_controller.game_data import validate_rom
from memory_state.game_state import PokemonGameState

ROOT = Path(__file__).resolve().parents[1]


class BoundedSession(EmulatorSession):
    """Fixture-wide guard against setup or controller loops hanging the test."""

    def __init__(self, emulator):
        super().__init__(emulator)
        self.frames = 0

    def tick(self, count=1, *args, **kwargs):
        self.frames += count
        if self.frames > 80000:
            raise TimeoutError("Battle integration test exceeded 80,000 frames")
        return super().tick(count, *args, **kwargs)


@pytest.fixture
def rival_battle():
    rom, save = ROOT / "Pokemon_Red/Red.gb", ROOT / "saves/in-room-start.state"
    missing = [str(path) for path in (rom, save) if not path.is_file()]
    if missing:
        pytest.skip("Local assets required: " + ", ".join(missing))
    validate_rom(rom)
    emulator = PyBoy(str(rom), window="null", sound_emulated=False)
    try:
        emulator.set_emulation_speed(0)
        with save.open("rb") as stream:
            emulator.load_state(stream)
        session = BoundedSession(emulator)
        session.tick()
        state = PokemonGameState(session)
        navigator = AutonomousController(session, state, starter="charmander")
        # Reach the first trainer battle through gameplay; don't alter game RAM.
        with pytest.raises(BattleInterrupt):
            navigator.go_to("ROUTE_1")
        battle = BattleController(session, state)
        assert battle.wait_for_turn(timeout=6000), "Setup did not reach the battle menu"
        assert state.mem.read_byte(0xD057) == 2, "Expected the rival trainer battle"
        assert state.mem.read_byte(0xCC2F) == 0, "Expected the starter to be active"
        # Party slot 0 must contain Scratch followed by Growl.
        assert state.mem.read_bytes(0xD173, 2) == [10, 45]
        yield session, state, battle
    finally:
        emulator.stop(save=False)


@pytest.mark.integration
def test_fight_consumes_requested_move_after_remembered_selection(rival_battle):
    session, state, battle = rival_battle
    # First select Growl, then demand Scratch twice. This covers both a remembered
    # different slot and consecutive requests for the same slot in the real menu.
    for turn, requested in enumerate((1, 0, 0), start=1):
        assert battle.is_player_turn(), f"Turn {turn}: main menu not ready"
        before = state.mem.read_bytes(0xD188, 4)  # party slot 0 move PP
        assert battle.fight(requested), f"Turn {turn}: action was rejected"
        # Don't accept fight() returning True as evidence. Advance until the
        # engine actually consumes PP; stale menu text must not end this check.
        for _ in range(2400):
            session.tick()
            after = state.mem.read_bytes(0xD188, 4)
            if after != before:
                break
        else:
            pytest.fail(f"Turn {turn}: no move consumed PP after requesting slot {requested}")
        expected = before.copy()
        expected[requested] -= 1
        assert after == expected, (
            f"Turn {turn}: requested slot {requested}; PP before={before}, after={after}. "
            "A different move was used or the selected slot was consumed incorrectly."
        )
        if turn < 3:
            assert battle.wait_for_turn(timeout=6000), f"Turn {turn}: next main menu never appeared"
