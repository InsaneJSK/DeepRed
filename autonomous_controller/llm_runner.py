"""Bounded model loop and a small Ollama adapter; no model SDK required."""

import json
import time
from collections import Counter, deque
from urllib.request import Request, urlopen

SYSTEM_PROMPT = """You play Pokemon Red through RAM observations and validated actions.
Choose ONE action as JSON matching the supplied schema. No prose or markdown.
Use only currently available actions. Indices are zero-based, except NPC object
slots, which are provided explicitly. Choose a move on each battle turn.
navigate takes the FINAL destination (e.g. ROUTE_1, VIRIDIAN_CITY,
VIRIDIAN_POKECENTER, OAKS_LAB, REDS_HOUSE_2F). The controller handles all walking,
doors and intermediate maps. You do not choose each doorway. Story gates may
block travel; consider dialogue and previous outcomes rather than repeating a
blocked action. Exits describe local surroundings, not a restriction on goals.
Mandatory dialogue and animations advance automatically. Interrupted travel
normally resumes after battle. Menu choices, purchases and battles need you.
Use finish with a brief reason when the user's goal is achieved or you cannot
proceed. Do not claim success without evidence from the observation.
"""


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
    goal,
    log,
    *,
    max_calls=30,
    max_actions=200,
    auto_resume=True,
    stop_at=None,
    request=None,
    checkpoint=None,
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
    failures = automatic_streak = actions = no_progress = 0
    resume_ready = False
    started = time.monotonic()

    def record(event, **data):
        log.write(json.dumps({"event": event, **data}, ensure_ascii=False) + "\n")
        log.flush()

    record(
        "start",
        goal=goal,
        model=client.model,
        max_calls=max_calls,
        max_actions=max_actions,
        auto_resume=auto_resume,
        stop_at=stop_at,
    )
    try:
        while actions < max_actions:
            observation = agent.observe()
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
                                "goal": goal,
                                "observation": observation,
                                "recent_outcomes": list(history),
                                "action_schema": schema,
                            }
                        ),
                    },
                ]
                stats["llm_calls"] += 1
                stats["calls_by_decision"][observation["decision"]] += 1
                record("request", call=stats["llm_calls"], messages=messages, schema=schema)
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
                        print("Model stopped:", action["reason"])
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
                automatic_streak += 1
                if automatic_streak > 8:
                    stats["stop_reason"] = "automatic_action_limit"
                    break
                stats["automatic_actions"][action["action"]] += 1
            else:
                stats["model_actions"] += 1
            result = agent.execute(action)
            actions += 1
            record("action", automatic=automatic, request=action, result=result)
            print(f"{'Auto' if automatic else 'Model'} {action}: {result['status']}", flush=True)
            history.append(
                {
                    "request": action,
                    "status": result["status"],
                    "detail": result["detail"],
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
        print("Run summary:", json.dumps(stats), flush=True)
    return stats
