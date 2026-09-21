"""Bounded model loop and a small Ollama adapter; no model SDK required."""

import json
import time
from collections import Counter, deque
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from urllib.request import Request, urlopen

from autonomous_controller.agent_memory import AgentMemory
from autonomous_controller.presentation import decision_text

PLAY_INSTRUCTION = "Reach VIRIDIAN_CITY."

SYSTEM_PROMPT = (
    PLAY_INSTRUCTION
    + """
You control Pokemon Red through RAM observations and validated actions.
Return exactly ONE JSON action matching action_schema. No prose or markdown.
Choose only actions listed in observation.actions, or finish as described below.

Prioritize reaching VIRIDIAN_CITY over exploring unrelated buildings.
When navigate is available and recent outcomes do not show an unresolved blocker,
request {"action":"navigate","destination":"VIRIDIAN_CITY"} directly.
The controller handles the full route, including walking, doors and intermediate
maps. Do not navigate one doorway at a time. Local exits are observations, not a
list of the only destinations you may request.

If travel is blocked or interrupted, use the current decision, dialogue and
recent_outcomes to address the obstacle. Choose a starter when starter_options
are offered. Resolve battles and menus before continuing the journey.
Mandatory dialogue advances automatically, and interrupted travel normally resumes
automatically. Use resume when available if the pending destination needs continuing.

Consult exploration_memory before revisiting a location. A completed trip only
confirms movement, not goal progress. Do not repeat a rejected trip unchanged.
Instead, choose a relevant interaction or a different destination to address the
blocker. Return to earlier locations when new observations give a reason to do so.

Use battle.kind to distinguish encounters. In wild battles, always run away.
Only if escape keeps failing, choose a usable move instead. In trainer
battles, use fight with a move that has PP. If a switch is
required, choose a healthy available party member. Respect the current menu and
available actions rather than assuming every battle action is always possible.

Indices are zero-based except NPC object slots, which are provided explicitly.
Use finish with a brief reason only when the observed map is VIRIDIAN_CITY and
the decision is overworld, or when no supported action can resolve a blocker.
Do not claim arrival just because a navigation request was submitted.
"""
)


class OllamaClient:
    def __init__(
        self, model="qwen3:4b", base_url="http://localhost:11434", timeout=180, think=False
    ):
        self.model, self.base_url, self.timeout = model, base_url.rstrip("/"), timeout
        self.think = think

    def request(self, messages, schema):
        payload = {
            "model": self.model,
            "messages": messages,
            "format": schema,
            "stream": False,
            "think": self.think,
            "options": {
                "temperature": 0,
                "num_predict": 2048 if self.think else 256,
                "num_ctx": 8192,
            },
        }
        request = Request(
            self.base_url + "/api/chat",
            json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        # No hidden retries: every HTTP attempt is one counted model call.
        with urlopen(request, timeout=self.timeout) as response:
            return json.load(response)


def decision_schema(agent, observation):
    variants = [
        variant
        for variant in agent.action_schema()["oneOf"]
        if variant["properties"]["action"]["const"] in observation["actions"]
    ]
    variants.append(
        {
            "type": "object",
            "properties": {"action": {"const": "finish"}, "reason": {"type": "string"}},
            "required": ["action", "reason"],
            "additionalProperties": False,
        }
    )
    return {"oneOf": variants}


def run_agent(
    agent,
    client,
    log,
    *,
    max_calls=30,
    max_actions=200,
    auto_resume=True,
    auto_flee=False,
    stop_at=None,
    request=None,
    checkpoint=None,
    on_decision=None,
    on_automatic=None,
    diagnostics=None,
    verbose=False,
):
    """Count attempts, preserve outcomes, and never ask a model to advance text.

    request optionally pumps a paused game window around the HTTP call. Logs
    contain exactly the prompts sent, responses and emulator action results.
    """
    stats = {
        "llm_calls": 0,
        "calls_by_decision": Counter(),
        "model_actions": 0,
        "automatic_actions": Counter(),
        "prompt_tokens": 0,
        "output_tokens": 0,
        "responses_with_usage": 0,
        "llm_seconds": 0.0,
        "stop_reason": None,
    }
    history = deque(maxlen=6)
    memory = AgentMemory()
    failures = automatic_streak = actions = no_progress = 0
    resume_ready = False
    flee_attempts = 0
    started = time.monotonic()

    def record(event, **data):
        log.write(json.dumps({"event": event, **data}, ensure_ascii=False) + "\n")
        log.flush()

    record(
        "start",
        instruction=PLAY_INSTRUCTION,
        model=client.model,
        max_calls=max_calls,
        max_actions=max_actions,
        auto_resume=auto_resume,
        auto_flee=auto_flee,
        stop_at=stop_at,
    )
    try:
        while actions < max_actions:
            observation = agent.observe()
            if not observation.get("battle", {}).get("active", False):
                flee_attempts = 0
            memory.observe(observation)
            if (
                stop_at
                and observation["location"]["map_key"] == stop_at
                and observation["decision"] == "overworld"
            ):
                stats["stop_reason"] = "destination_reached"
                record("destination_reached", observation=observation)
                break
            available = observation["actions"]
            automatic = False
            if available == ["advance"]:
                action, automatic = {"action": "advance"}, True
            elif auto_resume and resume_ready and "resume" in available:
                action, automatic = {"action": "resume"}, True
                resume_ready = False
            elif (
                auto_flee
                and flee_attempts < 2
                and observation.get("battle", {}).get("active")
                and observation["battle"].get("kind") == "wild"
                and observation["decision"] == "battle"
                and "run" in available
            ):
                action, automatic = {"action": "run"}, True
            else:
                automatic_streak = 0
                if not available:
                    stats["stop_reason"] = "unsupported_decision"
                    break
                if stats["llm_calls"] >= max_calls:
                    stats["stop_reason"] = "call_limit"
                    break
                schema = decision_schema(agent, observation)
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "observation": observation,
                                "recent_outcomes": list(history),
                                "exploration_memory": memory.summary(),
                                "action_schema": schema,
                                **(
                                    {
                                        "automatic_policy": {
                                            "auto_flee_attempts_this_encounter": flee_attempts,
                                            "limit": 2,
                                            "note": "Automatic escape attempts are bounded. If still in battle after two attempts, choose how to proceed; fighting is allowed.",
                                        }
                                    }
                                    if auto_flee
                                    else {}
                                ),
                            }
                        ),
                    },
                ]
                stats["llm_calls"] += 1
                stats["calls_by_decision"][observation["decision"]] += 1
                record("request", call=stats["llm_calls"], messages=messages, schema=schema)
                if verbose:
                    print(
                        f"LLM call {stats['llm_calls']}/{max_calls}: {observation['decision']}",
                        flush=True,
                    )
                call_start = time.monotonic()
                try:

                    def perform():
                        return client.request(messages, schema)

                    response = request(perform) if request else perform()
                except (OSError, ValueError) as error:
                    stats["stop_reason"] = "model_error"
                    record("model_error", error=str(error))
                    print(f"Model request failed: {error}", flush=True)
                    break
                finally:
                    stats["llm_seconds"] += time.monotonic() - call_start
                record("response", call=stats["llm_calls"], response=response)
                if isinstance(response, dict):
                    counts = [response.get(k) for k in ("prompt_eval_count", "eval_count")]
                    if all(type(n) is int and n >= 0 for n in counts):
                        stats["prompt_tokens"] += counts[0]
                        stats["output_tokens"] += counts[1]
                        stats["responses_with_usage"] += 1
                try:
                    action = json.loads(response["message"]["content"])
                    if not isinstance(action, dict):
                        raise ValueError("Expected one JSON action object")
                    if action.get("action") == "finish":
                        if set(action) != {"action", "reason"} or not isinstance(
                            action["reason"], str
                        ):
                            raise ValueError("finish requires a reason string")
                        stats["stop_reason"] = "model_finished"
                        record("finish", reason=action["reason"], observation=observation)
                        print(
                            f"{stats['llm_calls']:02d}  {decision_text(action, observation)}",
                            flush=True,
                        )
                        if on_decision:
                            on_decision(action, observation, stats["llm_calls"])
                        break
                except (ValueError, KeyError, TypeError) as error:
                    failures += 1
                    history.append({"status": "invalid_response", "detail": str(error)})
                    record("invalid_response", detail=str(error))
                    if failures >= 3:
                        stats["stop_reason"] = "repeated_failures"
                        break
                    continue
            if automatic:
                if automatic_streak >= 8:
                    stats["stop_reason"] = "automatic_no_progress"
                    break
                stats["automatic_actions"][action["action"]] += 1
                if action["action"] == "run":
                    flee_attempts += 1
                    print(f"Auto: attempt escape ({flee_attempts}/2)", flush=True)
                    if on_automatic:
                        on_automatic(action, observation, stats["llm_calls"])
            else:
                stats["model_actions"] += 1
                print(f"{stats['llm_calls']:02d}  {decision_text(action, observation)}", flush=True)
                if on_decision:
                    on_decision(action, observation, stats["llm_calls"])
            with (
                nullcontext()
                if verbose
                else redirect_stdout(diagnostics if diagnostics is not None else StringIO())
            ):
                rejection = memory.rejection(action, observation)
                result = (
                    {"status": "rejected", "detail": rejection, "observation": observation}
                    if rejection
                    else agent.execute(action)
                )
            actions += 1
            if automatic:
                automatic_streak = (
                    automatic_streak + 1 if observation == result["observation"] else 0
                )
            record("action", automatic=automatic, request=action, result=result)
            if rejection:
                print(
                    "Navigation rejected: repeated trip; asking the model to choose again.",
                    flush=True,
                )
            if verbose:
                print(
                    f"{'Auto' if automatic else 'Model'} {action}: {result['status']}", flush=True
                )
            memory.record(action, observation, result)
            if not result["observation"].get("battle", {}).get("active", False):
                flee_attempts = 0
            if (
                not automatic
                or action["action"] == "run"
                or result["status"] in ("rejected", "blocked", "timeout", "failed")
            ):
                history.append(
                    {
                        "request": action,
                        "automatic": automatic,
                        "status": result["status"],
                        "detail": result["detail"],
                        "from_map": observation["location"].get("map_key"),
                        "location": result["observation"]["location"],
                    }
                )
            if action["action"] in ("navigate", "resume"):
                resume_ready = result["status"] == "interrupted"
            failures = (
                failures + 1
                if result["status"] in ("rejected", "blocked", "timeout", "failed")
                else 0
            )
            if automatic and failures and action["action"] == "advance":
                stats["stop_reason"] = "automatic_action_failed"
                break
            if failures >= 3:
                stats["stop_reason"] = "repeated_failures"
                break
            no_progress = (
                no_progress + 1 if not automatic and observation == result["observation"] else 0
            )
            if no_progress >= 3:
                stats["stop_reason"] = "no_progress"
                break
            if checkpoint and actions % 10 == 0:
                checkpoint()
        else:
            stats["stop_reason"] = "action_limit"
    finally:
        stats["stop_reason"] = stats["stop_reason"] or "interrupted_or_error"
        stats["elapsed_seconds"] = time.monotonic() - started
        record("summary", **stats)
        if verbose:
            print("Run summary:", json.dumps(stats), flush=True)
        else:
            print(
                f"Stopped: {stats['stop_reason'].replace('_', ' ')} ({stats['llm_calls']} calls).",
                flush=True,
            )
    return stats
