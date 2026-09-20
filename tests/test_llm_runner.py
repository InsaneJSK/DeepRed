"""Count model decisions separately from controller work and bound failures."""

import json
from io import StringIO
from unittest.mock import Mock

from autonomous_controller.agent_interface import AgentInterface
from autonomous_controller.llm_runner import OllamaClient, run_agent


def observation(actions, decision="overworld"):
    return {"actions": actions, "decision": decision, "location": {"map_key": "PALLET_TOWN"}}


def response(action):
    return {"message": {"content": json.dumps(action)}, "prompt_eval_count": 100, "eval_count": 10}


def test_runner_navigation_battle_and_automatic_continuation():
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
    stats = run_agent(agent, client, "Reach Route 1", log)
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
    arrived_agent = Mock()
    arrived_agent.observe.return_value = world
    client.reset_mock()
    stats = run_agent(arrived_agent, client, "Arrive", StringIO(), stop_at="PALLET_TOWN")
    assert stats["stop_reason"] == "destination_reached"
    assert stats["llm_calls"] == 0
    client.request.assert_not_called()
    arrived_agent.execute.assert_not_called()


def test_runner_limits_and_errors(subtests):
    for scenario, expected, count in (
        ("invalid_json", "repeated_failures", 3),
        ("blocked", "repeated_failures", 3),
        ("network", "model_error", 1),
        ("call_limit", "call_limit", 1),
        ("no_progress", "no_progress", 3),
        ("automatic_limit", "automatic_action_limit", 0),
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
                agent, client, "Travel", StringIO(), max_calls=1 if scenario == "call_limit" else 30
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

    from threading import Event

    import pytest
    from pyboy.utils import WindowEvent

    from ai_demo import paused_request
    from autonomous_controller.emulator_session import EmulatorClosed

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
