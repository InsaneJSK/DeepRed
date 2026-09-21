"""Compact, run-local observations of exploration and repeated travel."""

import json
from collections import Counter, deque


class AgentMemory:
    TRIP_LIMIT = 1  # Reject the second unchanged trip; adjust this one setting.

    def __init__(self):
        self.visits = Counter()
        self.transitions = Counter()
        self.events = deque(maxlen=8)
        self.dialogue_seen = set()
        self.interactions = set()
        self.last_map = None
        self.state = None

    def observe(self, observation):
        """Ignore coordinates, facing and moving sprites when assessing change."""
        current_map = observation["location"].get("map_key")
        changed = False
        if current_map and current_map != self.last_map:
            changed = current_map not in self.visits
            self.visits[current_map] += 1
            self.last_map = current_map
        state = {key: observation.get(key) for key in ("party", "inventory", "money", "badges")}
        signature = json.dumps(state, sort_keys=True)
        if self.state is not None and self.state != signature:
            previous = json.loads(self.state)
            self.events.append(
                {
                    "map": current_map,
                    "changed_fields": [key for key in state if state[key] != previous[key]],
                }
            )
            changed = True
        self.state = signature
        dialogue = observation.get("dialogue", "").strip()
        key = (current_map, dialogue)
        if dialogue and key not in self.dialogue_seen:
            self.dialogue_seen.add(key)
            self.events.append({"map": current_map, "dialogue": dialogue[:320]})
            changed = True
        if changed:
            self.transitions.clear()
        return changed

    def record(self, action, before, result):
        after = result["observation"]
        self.observe(after)
        source = before["location"].get("map_key")
        target = after["location"].get("map_key")
        successful = result["status"] in ("completed", "submitted", "interrupted")
        interaction = (source, action.get("object_slot"))
        if action["action"] == "interact" and successful and interaction not in self.interactions:
            self.interactions.add(interaction)
            self.events.append({"map": source, "interaction": action, "detail": result["detail"]})
            self.transitions.clear()
        if (
            action["action"] in ("navigate", "resume")
            and result["status"] == "completed"
            and source
            and target
            and source != target
            and after["decision"] == "overworld"
        ):
            self.transitions[(source, target)] += 1

    def rejection(self, action, observation):
        target = (
            action.get("destination")
            if action.get("action") == "navigate"
            else (
                observation.get("pending_destination") if action.get("action") == "resume" else None
            )
        )
        if not isinstance(target, str):
            return None
        source = observation["location"].get("map_key")
        target = target.upper()
        count = self.transitions[(source, target)]
        if count >= self.TRIP_LIMIT:
            return (
                f"Navigation rejected: {source} -> {target} has already been travelled {count} time(s) "
                "without new progress. You have not moved. Choose another destination or "
                "interact here; this trip becomes available after new observed progress."
            )
        return None

    def summary(self):
        repeated = [
            {"from": source, "to": target, "count": count}
            for (source, target), count in self.transitions.items()
            if count >= self.TRIP_LIMIT
        ]
        return {
            "map_visits": dict(self.visits),
            "notable_outcomes": list(self.events),
            "repeated_travel": repeated,
            "warning": (
                "Repeated travel with no new observed state, dialogue, place or NPC interaction. "
                "Choose a different destination or interaction; completing movement is not goal progress. "
                "Repeating a listed trip will be rejected before movement."
                if repeated
                else None
            ),
        }
