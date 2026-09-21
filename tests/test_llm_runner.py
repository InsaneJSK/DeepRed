"""Count model decisions separately from controller work and bound failures."""

import json
from io import StringIO
from unittest.mock import Mock

from autonomous_controller.agent_interface import AgentInterface
from autonomous_controller.llm_runner import PLAY_INSTRUCTION, OllamaClient, run_agent


def observation(actions, decision="overworld"):
    return {"actions": actions, "decision": decision, "location": {"map_key": "PALLET_TOWN"}}


def response(action):
    return {"message": {"content": json.dumps(action)}, "prompt_eval_count": 100, "eval_count": 10}


def test_runner_navigation_battle_and_automatic_continuation(capsys):
    world = observation(["navigate", "interact"])
    battle = observation(["fight", "run"], "battle")
    text = observation(["advance"], "advance")
    resume = observation(["navigate", "interact", "resume"])
    agent = Mock()
    agent.action_schema.side_effect = AgentInterface.action_schema
    agent.observe.side_effect = [world, battle, text, resume, world]
    agent.execute.side_effect = [
        {"status": status, "detail": None, "observation": obs}
        for status, obs in (
            ("interrupted", battle),
            ("submitted", text),
            ("completed", resume),
            ("completed", world),
        )
    ]
    client = Mock(model="test")
    client.request.side_effect = [
        response({"action": "navigate", "destination": "ROUTE_1"}),
        response({"action": "fight", "move_index": 0}),
        response({"action": "finish", "reason": "Arrived"}),
    ]
    log = StringIO()
    stats = run_agent(agent, client, log, stop_at="HIDDEN_STOP_TARGET")
    for call in client.request.call_args_list:
        messages, schema = call.args
        assert PLAY_INSTRUCTION in messages[0]["content"]
        assert "HIDDEN_STOP_TARGET" not in json.dumps([messages, schema])
        assert set(json.loads(messages[1]["content"])) == {
            "observation",
            "recent_outcomes",
            "exploration_memory",
            "action_schema",
        }
    assert stats["llm_calls"] == 3
    assert stats["prompt_tokens"] == 300
    assert stats["output_tokens"] == 30
    assert stats["automatic_actions"] == {"advance": 1, "resume": 1}
    assert stats["calls_by_decision"] == {"overworld": 2, "battle": 1}
    assert [call.args[0]["action"] for call in agent.execute.call_args_list] == [
        "navigate",
        "fight",
        "advance",
        "resume",
    ]
    assert json.loads(log.getvalue().splitlines()[-1])["stop_reason"] == "model_finished"
    output = capsys.readouterr().out
    assert "Go to Route 1" in output
    assert "Auto" not in output and "Run summary:" not in output
    arrived_agent = Mock()
    arrived_agent.observe.return_value = world
    client.reset_mock()
    stats = run_agent(arrived_agent, client, StringIO(), stop_at="PALLET_TOWN")
    assert stats["stop_reason"] == "destination_reached"
    assert stats["llm_calls"] == 0
    client.request.assert_not_called()
    arrived_agent.execute.assert_not_called()


def test_navigation_cycle_feedback_and_progress(subtests):
    from copy import deepcopy

    # Replay the observed Pallet/Blue's-house loop; progress and new interactions
    # must give legitimate return trips a fresh chance.
    for progress in (None, "stubborn", "inventory", "party", "dialogue", "interaction", "new_map"):
        with subtests.test(progress=progress):
            current = observation(["navigate", "interact"])
            current.update(inventory=[], party=[], dialogue="")
            agent, client = Mock(), Mock(model="test")
            agent.action_schema.side_effect = AgentInterface.action_schema
            agent.observe.side_effect = lambda: deepcopy(current)
            step = 0

            def execute(action):
                nonlocal step
                step += 1
                current["location"]["map_key"] = action.get(
                    "destination", current["location"]["map_key"]
                )
                # Facing changes are not progress; genuine inventory/party/text
                # changes are, even when a trip returns to a visited map.
                current["location"]["player_facing"] = str(step)
                if step == 2:
                    if progress == "inventory":
                        current["inventory"] = [{"name": "OAKS_PARCEL", "quantity": 1}]
                    elif progress == "party":
                        current["party"] = [{"species": "BULBASAUR", "level": 5}]
                    elif progress == "dialogue":
                        current["dialogue"] = "Please deliver this to Oak."
                return {"status": "completed", "detail": None, "observation": deepcopy(current)}

            agent.execute.side_effect = execute
            choices = [
                {"action": "navigate", "destination": name}
                for name in ["BLUES_HOUSE", "PALLET_TOWN"] * 2
            ]
            if progress == "interaction":
                choices.insert(2, {"action": "interact", "object_slot": 1})
            if progress == "new_map":
                choices[2]["destination"] = "ROUTE_1"
            if progress in ("inventory", "party", "dialogue"):
                choices = choices[:3]  # The newly justified return to Blue's house.
            if progress is None:
                choices[3]["destination"] = "ROUTE_1"
            if progress == "stubborn":
                choices = choices[:2] + [{"action": "navigate", "destination": "blues_house"}] * 3
            client.request.side_effect = [response(choice) for choice in choices] + [
                response({"action": "finish", "reason": "Test complete"})
            ]
            stats = run_agent(agent, client, StringIO())
            assert stats["stop_reason"] == (
                "repeated_failures" if progress == "stubborn" else "model_finished"
            )
            requests = [
                json.loads(call.args[0][1]["content"]) for call in client.request.call_args_list
            ]
            if progress is None:
                assert stats["llm_calls"] == 5
                assert agent.execute.call_count == 3
                assert requests[3]["observation"]["location"]["map_key"] == "PALLET_TOWN"
                last = requests[3]["recent_outcomes"][-1]
                assert last["status"] == "rejected" and "You have not moved" in last["detail"]
                assert requests[3]["exploration_memory"]["repeated_travel"]
                assert [c.args[0]["destination"] for c in agent.execute.call_args_list] == [
                    "BLUES_HOUSE",
                    "PALLET_TOWN",
                    "ROUTE_1",
                ]
            elif progress == "stubborn":
                assert stats["llm_calls"] == 5 and agent.execute.call_count == 2
            else:
                assert agent.execute.call_count == len(choices)


def test_exploration_memory_retains_context_through_automatic_text():
    from copy import deepcopy

    from autonomous_controller.agent_memory import AgentMemory

    memory = AgentMemory()
    before = observation(["navigate"])
    memory.observe(before)
    after = deepcopy(before)
    after["location"]["map_key"] = "BLUES_HOUSE"
    after["dialogue"] = "Visit my brother."
    memory.record(
        {"action": "navigate", "destination": "BLUES_HOUSE"},
        before,
        {"status": "completed", "detail": None, "observation": after},
    )
    for _ in range(10):
        memory.observe(after)
    summary = memory.summary()
    assert summary["map_visits"] == {"PALLET_TOWN": 1, "BLUES_HOUSE": 1}
    assert summary["notable_outcomes"] == [{"map": "BLUES_HOUSE", "dialogue": "Visit my brother."}]

    # Seven automatic text actions must not evict the initiating model action.
    agent, client = Mock(), Mock(model="test")
    agent.action_schema.side_effect = AgentInterface.action_schema
    text = observation(["advance"], "advance")
    agent.observe.side_effect = [before] + [text] * 7 + [before]
    agent.execute.side_effect = [
        {"status": "completed", "detail": None, "observation": obs} for obs in [text] * 7 + [before]
    ]
    client.request.side_effect = [
        response({"action": "navigate", "destination": "BLUES_HOUSE"}),
        response({"action": "finish", "reason": "Done"}),
    ]
    run_agent(agent, client, StringIO())
    payload = json.loads(client.request.call_args.args[0][1]["content"])
    assert len(payload["recent_outcomes"]) == 1
    assert payload["recent_outcomes"][0]["request"]["action"] == "navigate"


def test_runner_limits_and_errors(subtests):
    for scenario, expected, count in (
        ("invalid_json", "repeated_failures", 3),
        ("blocked", "repeated_failures", 3),
        ("network", "model_error", 1),
        ("call_limit", "call_limit", 1),
        ("no_progress", "no_progress", 3),
        ("automatic_limit", "automatic_no_progress", 0),
        ("unsupported", "unsupported_decision", 0),
    ):
        with subtests.test(scenario=scenario):
            agent, client = Mock(), Mock(model="test")
            agent.action_schema.side_effect = AgentInterface.action_schema
            obs = observation(
                []
                if scenario == "unsupported"
                else ["advance"]
                if scenario == "automatic_limit"
                else ["navigate"]
            )
            agent.observe.return_value = obs
            agent.execute.return_value = {
                "status": "blocked" if scenario == "blocked" else "completed",
                "detail": None,
                "observation": obs,
            }
            client.request.return_value = response({"action": "navigate", "destination": "ROUTE_1"})
            if scenario == "invalid_json":
                client.request.return_value = {"message": {"content": "bad"}}
            if scenario == "network":
                client.request.side_effect = OSError("unavailable")
            stats = run_agent(
                agent, client, StringIO(), max_calls=1 if scenario == "call_limit" else 30
            )
            assert stats["stop_reason"] == expected
            assert stats["llm_calls"] == count
            assert client.request.call_count == count
            if scenario in ("invalid_json", "network", "unsupported"):
                agent.execute.assert_not_called()
            if scenario == "automatic_limit":
                assert agent.execute.call_count == 8


def test_ollama_request_contract(monkeypatch):
    from contextlib import nullcontext

    returned = response({"action": "finish", "reason": "Done"})
    transport = Mock(return_value=nullcontext(StringIO(json.dumps(returned))))
    monkeypatch.setattr("autonomous_controller.llm_runner.urlopen", transport)
    client = OllamaClient("local-test", "http://localhost:11434/", 15)
    assert client.request([], {"oneOf": []}) == returned
    request = transport.call_args.args[0]
    assert request.full_url == "http://localhost:11434/api/chat"
    payload = json.loads(request.data)
    assert payload["model"] == "local-test"
    assert payload["stream"] is False and payload["think"] is False
    assert payload["format"] == {"oneOf": []}
    assert transport.call_count == 1

    from ai_demo import build_parser
    from autonomous_controller.presentation import DecisionWindow, PresentedSession, decision_text

    defaults = build_parser().parse_args([])
    assert not defaults.headless and defaults.overlay
    assert not defaults.auto_flee
    assert build_parser().parse_args(["--auto-flee"]).auto_flee
    assert defaults.model == "gemma3:4b" and defaults.max_calls == 30
    assert not hasattr(defaults, "goal")
    assert defaults.stop_at == "VIRIDIAN_CITY"
    assert build_parser().parse_args(["--no-stop-at"]).stop_at is None
    assert build_parser().parse_args(["--stop-at", "route_1"]).stop_at == "ROUTE_1"
    assert (
        decision_text(
            {"action": "fight", "move_index": 0},
            {"battle": {"player": {"moves": [{"index": 0, "name": "SCRATCH"}]}}},
        )
        == "Use Scratch"
    )

    from threading import Event

    import pytest
    from pyboy.utils import WindowEvent

    from ai_demo import paused_request
    from autonomous_controller.emulator_session import EmulatorClosed

    emulator, window = Mock(), Mock()
    window.pump.side_effect = EmulatorClosed()
    with pytest.raises(EmulatorClosed):
        PresentedSession(emulator, window).tick(100)
    emulator.tick.assert_called_once_with(1)

    speed_window = DecisionWindow.__new__(DecisionWindow)
    speed_window.session = Mock(paused=False)
    speed_window.fast = False
    speed_window.toggle_speed()
    speed_window.session.set_emulation_speed.assert_called_once_with(0)
    speed_window.session.reset_mock()
    speed_window.session.paused = True
    speed_window.toggle_speed()  # Changing speed during inference must survive unpause.
    speed_window.session.set_emulation_speed.assert_not_called()
    speed_window.session.paused = False
    speed_window.apply_speed()
    speed_window.session.set_emulation_speed.assert_called_once_with(1)

    released = Event()
    session = Mock()
    session.tick.side_effect = [True, EmulatorClosed()]
    try:
        with pytest.raises(EmulatorClosed):
            paused_request(session, lambda: released.wait(2))
    finally:
        released.set()
    assert [c.args[0] for c in session.send_input.call_args_list] == [
        WindowEvent.PAUSE,
        WindowEvent.UNPAUSE,
    ]


def test_optional_auto_flee_policy(subtests, capsys):
    from collections import deque
    from copy import deepcopy

    from autonomous_controller.presentation import DecisionWindow

    world = observation(["navigate"])
    world["battle"] = {"active": False, "kind": None}
    wild = observation(["fight", "run"], "battle")
    wild["battle"] = {"active": True, "kind": "wild"}
    text = deepcopy(wild)
    text.update(actions=["advance"], decision="advance")
    for enabled, kind, decision, actions in (
        (False, "wild", "battle", ["fight", "run"]),
        (True, "trainer", "battle", ["fight", "run"]),
        (True, "tutorial", "battle", ["fight", "run"]),
        (True, "wild", "switch", ["switch"]),
        (True, "wild", "menu", ["cancel", "run"]),
    ):
        with subtests.test(enabled=enabled, kind=kind, decision=decision):
            obs = deepcopy(wild)
            obs.update(actions=actions, decision=decision)
            obs["battle"]["kind"] = kind
            agent, client = Mock(), Mock(model="test")
            agent.observe.return_value = obs
            agent.action_schema.side_effect = AgentInterface.action_schema
            client.request.return_value = response({"action": "finish", "reason": "Test"})
            stats = run_agent(agent, client, StringIO(), auto_flee=enabled)
            assert stats["llm_calls"] == 1 and not stats["automatic_actions"]
            agent.execute.assert_not_called()

    agent, client = Mock(), Mock(model="test")
    agent.action_schema.side_effect = AgentInterface.action_schema
    # Two unsuccessful attempts with intervening text, then model control;
    # a later wild encounter gets a fresh attempt budget.
    sequence = [wild, text, wild, text, wild, world, wild, world]
    agent.observe.side_effect = sequence
    agent.execute.side_effect = [
        {"status": "submitted", "detail": None, "observation": deepcopy(obs)}
        for obs in sequence[1:]
    ]
    client.request.side_effect = [
        response({"action": "fight", "move_index": 0}),
        response({"action": "navigate", "destination": "ROUTE_1"}),
        response({"action": "finish", "reason": "Done"}),
    ]
    notify, choices = Mock(), Mock()
    log = StringIO()
    stats = run_agent(agent, client, log, auto_flee=True, on_automatic=notify, on_decision=choices)
    assert [c.args[0]["action"] for c in agent.execute.call_args_list] == [
        "run",
        "advance",
        "run",
        "advance",
        "fight",
        "navigate",
        "run",
    ]
    assert stats["llm_calls"] == 3 and stats["automatic_actions"] == {"run": 3, "advance": 2}
    assert notify.call_count == 3
    assert all(c.args[0]["action"] != "run" for c in choices.call_args_list)
    prompt = json.loads(client.request.call_args_list[0].args[0][1]["content"])
    assert prompt["automatic_policy"]["auto_flee_attempts_this_encounter"] == 2
    assert [o["request"]["action"] for o in prompt["recent_outcomes"]] == ["run", "run"]
    assert all(o["automatic"] for o in prompt["recent_outcomes"])
    entries = [json.loads(line) for line in log.getvalue().splitlines()]
    assert entries[0]["auto_flee"] is True
    assert all(
        e["automatic"]
        for e in entries
        if e["event"] == "action" and e["request"]["action"] == "run"
    )
    assert "Auto: attempt escape (2/2)" in capsys.readouterr().out

    window = DecisionWindow.__new__(DecisionWindow)
    window.history, window.pump = deque(maxlen=5), Mock()
    window.automatic({"action": "run"}, wild, 4)
    assert window.actor == "AUTO POLICY" and window.calls == 4
    assert window.history[-1] == (None, "Auto: attempt escape")
    window.choose({"action": "fight", "move_index": 0}, wild, 5)
    assert window.actor == "AI CHOSE" and window.calls == 5

    # Several encounters can legitimately chain more than eight automatic
    # actions without another model decision. Movement/escape is real progress.
    arrived = deepcopy(world)
    arrived["location"]["map_key"] = "VIRIDIAN_CITY"
    resume = deepcopy(world)
    resume["actions"] = ["navigate", "resume"]
    sequence = [world] + [wild, text, resume] * 4 + [arrived]
    agent, client = Mock(), Mock(model="test")
    agent.action_schema.side_effect = AgentInterface.action_schema
    agent.observe.side_effect = sequence
    agent.execute.side_effect = [
        {
            "status": "interrupted" if obs is wild else "completed",
            "detail": None,
            "observation": deepcopy(obs),
        }
        for obs in sequence[1:]
    ]
    client.request.return_value = response({"action": "navigate", "destination": "VIRIDIAN_CITY"})
    stats = run_agent(agent, client, StringIO(), auto_flee=True, stop_at="VIRIDIAN_CITY")
    assert stats["stop_reason"] == "destination_reached" and stats["llm_calls"] == 1
    assert stats["automatic_actions"] == {"run": 4, "advance": 4, "resume": 4}
