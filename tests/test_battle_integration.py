"""One real-game journey: starter nickname decision and battle move choices."""

import json
import os
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from pyboy import PyBoy
from pyboy.utils import WindowEvent

from autonomous_controller import AutonomousController, BattleController, BattleInterrupt
from autonomous_controller.agent_interface import AgentInterface
from autonomous_controller.emulator_session import EmulatorSession
from autonomous_controller.game_data import validate_rom
from autonomous_controller.llm_runner import run_agent
from memory_state.game_state import PokemonGameState

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_auto_flee_exits_real_wild_encounters(subtests):
    """Use local, reproducible escape checkpoints; never mutate saved assets."""
    rom = ROOT / "Pokemon_Red/Red.gb"
    paths = [
        Path(p)
        for p in os.environ.get("DEEPRED_WILD_STATES", str(ROOT / "saves/wild-escape.state")).split(
            os.pathsep
        )
    ]
    if not rom.is_file() or any(not p.is_file() for p in paths):
        pytest.skip("Local ROM and wild-escape checkpoint(s) required")
    validate_rom(rom)
    emulator = PyBoy(str(rom), window="null", sound_emulated=False)
    try:
        emulator.set_emulation_speed(0)
        for path in paths:
            with subtests.test(checkpoint=path.name):
                with path.open("rb") as stream:
                    emulator.load_state(stream)
                session = BoundedSession(emulator)
                agent = AgentInterface(session, PokemonGameState(session))
                assert agent.observe()["battle"]["kind"] == "wild"
                for _ in range(8):
                    if agent.observe()["decision"] == "battle":
                        break
                    assert agent.execute({"action": "advance"})["status"] == "completed"
                before = agent.observe()
                assert before["decision"] == "battle"
                client, log = Mock(model="not-called"), StringIO()
                client.request.side_effect = AssertionError(
                    "Escape should not require model inference"
                )
                stats = run_agent(
                    agent,
                    client,
                    log,
                    auto_flee=True,
                    stop_at=before["location"]["map_key"],
                    max_actions=16,
                )
                after = agent.observe()
                assert stats["stop_reason"] == "destination_reached"
                assert 1 <= stats["automatic_actions"]["run"] <= 2
                assert not after["battle"]["active"] and after["decision"] == "overworld"
                assert after["party"][0]["moves"] == before["party"][0]["moves"]
                assert after["inventory"] == before["inventory"]
                assert "Got away safely!" in log.getvalue()
                client.request.assert_not_called()
    finally:
        emulator.stop(save=False)


@pytest.mark.integration
def test_explicit_starter_choices(subtests):
    rom, save = ROOT / "Pokemon_Red/Red.gb", ROOT / "saves/in-room-start.state"
    if not rom.exists() or not save.exists():
        pytest.skip("Local ROM and bedroom checkpoint required")
    validate_rom(rom)
    emulator = PyBoy(str(rom), window="null", sound_emulated=False)
    try:
        emulator.set_emulation_speed(0)
        with save.open("rb") as stream:
            emulator.load_state(stream)
        session = BoundedSession(emulator)
        session.tick()
        state = PokemonGameState(session)
        agent = AgentInterface(session, state)
        result = agent.execute({"action": "navigate", "destination": "ROUTE_1"})
        assert result["status"] == "interrupted"
        assert result["observation"]["decision"] == "starter"
        assert not state.party_pokemon
        assert agent.pending_destination == "ROUTE_1"
        checkpoint = BytesIO()
        emulator.save_state(checkpoint)
        for pokemon in ("bulbasaur", "charmander", "squirtle"):
            with subtests.test(pokemon=pokemon):
                emulator.load_state(BytesIO(checkpoint.getvalue()))
                agent = AgentInterface(session, state)
                assert agent.observe()["actions"] == ["choose_starter"]
                assert (
                    agent.execute({"action": "choose_starter", "pokemon": "pikachu"})["status"]
                    == "rejected"
                )
                # Selection remains correct when the starting position differs.
                assert agent.navigation.navigate_to_tile(4, 4)
                result = agent.execute({"action": "choose_starter", "pokemon": pokemon})
                assert result["status"] == "completed", result
                assert state.party_pokemon[0]["species_name"].lower() == pokemon
                assert state.party_pokemon[0]["nickname"].lower() == pokemon
                assert (
                    agent.execute({"action": "choose_starter", "pokemon": pokemon})["status"]
                    == "rejected"
                )
    finally:
        emulator.stop(save=False)


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


@pytest.mark.integration
def test_starter_nickname_and_battle_choices(subtests):
    rom, save = ROOT / "Pokemon_Red/Red.gb", ROOT / "saves/in-room-start.state"
    if not rom.is_file() or not save.is_file():
        pytest.skip("Local ROM and bedroom save required")
    validate_rom(rom)
    emulator = PyBoy(str(rom), window="null", sound_emulated=False)
    observations = {"prompt": False, "naming_screen": False, "choices": []}

    class ObservedSession(BoundedSession):
        state = None

        def observe(self):
            if self.state is None:
                return ""
            text = " ".join(self.state.dialog.upper().split())
            observations["prompt"] |= "NICKNAME" in text
            observations["naming_screen"] |= "LOWER CASE" in text or "UPPER CASE" in text
            return text

        def tick(self, count=1, *args, **kwargs):
            result = super().tick(count, *args, **kwargs)
            self.observe()
            return result

        def send_input(self, button, *args, **kwargs):
            text = self.observe()
            if (
                "NICKNAME" in text
                and "YES" in text
                and "NO" in text
                and button
                in (
                    WindowEvent.PRESS_BUTTON_A,
                    WindowEvent.PRESS_BUTTON_B,
                )
            ):
                choice = "A" if button == WindowEvent.PRESS_BUTTON_A else "B"
                observations["choices"] = (observations["choices"] + [(choice, text)])[-4:]
            return self._emulator.send_input(button, *args, **kwargs)

    try:
        emulator.set_emulation_speed(0)
        with save.open("rb") as stream:
            emulator.load_state(stream)
        session = ObservedSession(emulator)
        session.tick()
        state = PokemonGameState(session)
        session.state = state
        controller = AutonomousController(session, state, starter="charmander")
        # Reaching the rival battle proves the nickname prompt didn't trap us.
        with pytest.raises(BattleInterrupt):
            controller.go_to("ROUTE_1")
        assert observations["prompt"], "Setup never reached the nickname question"
        party = state.party_pokemon
        assert party and party[0]["species_name"] == "CHARMANDER"
        assert not observations["naming_screen"], (
            f"Controller accepted naming instead of declining; nickname={party[0]['nickname']!r}; "
            f"prompt inputs={observations['choices']!r}"
        )
        assert party[0]["nickname"] == "CHARMANDER", "Default name was replaced"
        battle = BattleController(session, state)
        assert battle.wait_for_turn(timeout=6000), "Setup did not reach the battle menu"
        assert state.mem.read_byte(0xD057) == 2, "Expected the rival trainer battle"
        assert state.mem.read_byte(0xCC2F) == 0, "Expected the starter to be active"
        # Party slot 0 must contain Scratch followed by Growl.
        assert state.mem.read_bytes(0xD173, 2) == [10, 45]
        agent = AgentInterface(session, state, navigation=controller, battle=battle)
        observation = agent.observe()
        assert json.loads(json.dumps(observation)) == observation
        assert observation["decision"] == "battle"
        assert observation["battle"]["opponent"]["species"] == "SQUIRTLE"
        assert observation["battle"]["opponent"]["hp"] > 0
        assert observation["battle"]["player"]["moves"][0]["name"] == "SCRATCH"
        checkpoint = BytesIO()
        emulator.save_state(checkpoint)
        # First select Growl, then demand Scratch twice. This covers both a remembered
        # different slot and consecutive requests for the same slot in the real menu.
        for turn, requested in enumerate((1, 0, 0), start=1):
            assert battle.is_player_turn(), f"Turn {turn}: main menu not ready"
            before = state.mem.read_bytes(0xD188, 4)  # party slot 0 move PP
            result = agent.execute({"action": "fight", "move_index": requested})
            assert result["status"] == "submitted", f"Turn {turn}: {result}"
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
                assert battle.wait_for_turn(timeout=6000), (
                    f"Turn {turn}: next main menu never appeared"
                )
        _check_party_and_bag(subtests, emulator, session, state, battle, checkpoint.getvalue())
    finally:
        emulator.stop(save=False)


def _check_party_and_bag(subtests, emulator, session, state, battle, checkpoint):
    """Branch the same journey in memory; seed inventory/party only in test setup.

    The actual game engine handles inputs, effects, fainting, and capture. No
    generated state or changed cartridge RAM is persisted to the user's assets.
    """

    def restore(*, wild=False):
        emulator.load_state(BytesIO(checkpoint))
        session.frames = 0  # each independent branch gets the same bounded budget
        emulator.memory[0xD163] = 2
        emulator.memory[0xD165] = emulator.memory[0xD164]
        emulator.memory[0xD166] = 255
        emulator.memory[0xD197:0xD1C3] = emulator.memory[0xD16B:0xD197]
        emulator.memory[0xD2C0:0xD2CB] = emulator.memory[0xD2B5:0xD2C0]
        # Six items require scrolling to reach Potion; five uses avoid index shifts.
        emulator.memory[0xD31D] = 6
        for index, item in enumerate((1, 11, 12, 13, 0x50, 20)):
            emulator.memory[0xD31E + index * 2] = item
            emulator.memory[0xD31F + index * 2] = 5
        emulator.memory[0xD32A] = 255
        if wild:
            emulator.memory[0xD057] = 1

    with subtests.test(menu="party", scenario="voluntary_switch"):
        restore()
        assert battle.switch(1)
        assert battle.active_mon_slot() == 1
        assert battle.wait_for_turn(6000)
        assert battle.is_player_turn()
        assert battle.fight(0)
        assert battle.wait_for_turn(6000)
        assert emulator.memory[0xD1B4] == 34  # switched-in reserve used Scratch
        assert emulator.memory[0xD188] == 35  # original lead did not

    for wild in (False, True):
        with subtests.test(menu="party", scenario="forced_switch", wild=wild):
            restore(wild=wild)
            for address in (0xD16C, 0xD16D, 0xD015, 0xD016):
                emulator.memory[address] = 0  # fainted party and active battle HP
            assert battle.fight(1)
            assert battle.wait_for_turn(6000)
            assert battle.needs_switch()
            assert not battle.cancel()
            assert battle.switch(1)
            assert battle.active_mon_slot() == 1
            assert battle.wait_for_turn(6000)
            assert not battle.needs_switch()

    for damaged in (True, False):
        with subtests.test(menu="bag", scenario="targeted_potion", damaged=damaged):
            restore()
            if damaged:
                emulator.memory[0xD199] = 1  # reserve HP: 1/20
            assert battle.use_item(5, party_index=1) is damaged
            assert emulator.memory[0xD199] == 20
            assert emulator.memory[0xD329] == (4 if damaged else 5)
            assert battle.last_result == ("used" if damaged else "rejected")
            assert battle.is_player_turn()

    with subtests.test(menu="bag", scenario="ether_move_target"):
        restore()
        emulator.memory[0xD1B4] = 1  # reserve Scratch PP
        assert battle.use_item(4, party_index=1, move_index=0)
        assert emulator.memory[0xD1B4] == 11
        assert emulator.memory[0xD327] == 4
        assert battle.is_player_turn()

    for guaranteed in (True, False):
        with subtests.test(menu="bag", scenario="capture", guaranteed=guaranteed):
            restore(wild=True)
            emulator.memory[0xD31E] = 1 if guaranteed else 4
            assert battle.use_item(0)
            assert emulator.memory[0xD31F] == 4
            assert battle.last_result == ("caught" if guaranteed else "not_caught")
            if guaranteed:
                assert not battle.is_in_battle()
                assert len(state.party_pokemon) == 3
                assert state.party_pokemon[-1]["nickname"] == "SQUIRTLE"
            else:
                assert battle.is_player_turn()
                assert len(state.party_pokemon) == 2

    for menu in ("moves", "bag", "party", "party_actions"):
        with subtests.test(menu=menu, scenario="cancel_without_committing"):
            restore()
            option = {"moves": 0, "bag": 2, "party": 1, "party_actions": 1}[menu]
            battle._navigate_main_menu(option)
            battle._press_a()
            assert battle._wait_menu("party" if menu == "party_actions" else menu)
            if menu == "party_actions":
                battle._press_a()
                assert battle._wait_menu(menu)
            assert battle.cancel()
            assert battle.is_player_turn()
            assert battle.active_mon_slot() == 0
            assert emulator.memory[0xD31F] == 5
            assert emulator.memory[0xD188] == 35

    with subtests.test(menu="main", scenario="run_attempt"):
        restore(wild=True)
        assert battle.run()
        ready = battle.wait_for_turn(6000)
        assert ready or not battle.is_in_battle()
        assert emulator.memory[0xD31F] == 5
