"""Provider-independent, JSON-compatible observations and controller actions.

Observation never advances emulation. Actions execute synchronously; the owner
keeps responsibility for emulator lifetime and window-close cancellation.
"""

from copy import deepcopy

from autonomous_controller.battle_controller import BattleController
from autonomous_controller.controller import AutonomousController
from autonomous_controller.interrupt_handler import BattleInterrupt
from autonomous_controller.overworld_menu import OverworldMenu
from memory_state.battle_constants import Move, StatusCondition
from memory_state.pokemon_constants import Pokemon
from memory_state.sprite_names import object_name

_ARGUMENTS = {
    "navigate": ({"destination": str}, {}),
    "interact": ({"object_slot": int}, {}),
    "choose": ({"option_index": int}, {}),
    "quantity": ({"amount": int}, {}),
    "resume": ({}, {}),
    "fight": ({"move_index": int}, {}),
    "switch": ({"party_index": int}, {}),
    "use_item": ({"bag_index": int}, {"party_index": int, "move_index": int}),
    "run": ({}, {}),
    "cancel": ({}, {}),
    "advance": ({}, {}),
}


class AgentInterface:
    """One interface per emulator session; indices are always zero-based.

    navigate accepts any known map; the controller routes through intermediate
    maps. Static destinations do not promise story access.
    Interrupted travel is retained until resume or a new navigation request.
    """

    def __init__(self, emulator, game_state, *, navigation=None, battle=None):
        self.gs = game_state
        self.navigation = navigation or AutonomousController(emulator, game_state)
        self.battle = battle or BattleController(emulator, game_state)
        self.overworld = OverworldMenu(emulator, game_state)
        self.pending_destination = None

    @staticmethod
    def action_schema() -> dict:
        """Portable JSON Schema; adapters may wrap it in their provider's tool format."""
        variants = []
        for name, (required, optional) in _ARGUMENTS.items():
            properties = {"action": {"const": name}}
            for key, kind in (required | optional).items():
                properties[key] = {"type": "string" if kind is str else "integer"}
                if kind is int:
                    properties[key]["minimum"] = 0
            if name == "navigate":
                properties["destination"]["description"] = (
                    "Final target map, not the next doorway. For a goal of reaching ROUTE_1, "
                    "use ROUTE_1 even from REDS_HOUSE_2F; the controller routes across maps."
                )
            variants.append(
                {
                    "type": "object",
                    "properties": properties,
                    "required": ["action", *required],
                    "additionalProperties": False,
                }
            )
        return {"oneOf": variants}

    def _exits(self, name):
        graph = self.navigation.graph
        exits = []
        for warp in graph.warps(name):
            destinations = warp.get("dest_map_candidates", [warp["dest_map"]])
            if not destinations or destinations == ["LAST_MAP"]:
                last = graph.map_name(self.gs.mem.read_byte(0xD365))
                destinations = [last] if last else []
            exits.append(
                {
                    "kind": "warp",
                    "position": {"x": warp["x"], "y": warp["y"]},
                    "destinations": list(destinations),
                    "access": "unverified",
                }
            )
        for direction, connection in graph.connections(name).items():
            exits.append(
                {
                    "kind": "connection",
                    "direction": direction,
                    "destinations": [connection["map"]],
                    "access": "unverified",
                }
            )
        return exits

    def _combatant(self, base):
        """Read the live battle struct, including transformed moves and current HP."""
        read = self.gs.mem.read_byte

        def name(enum, value):
            try:
                return enum(value).name
            except ValueError:
                return f"UNKNOWN_{value}"

        return {
            "species": name(Pokemon, read(base)),
            "hp": read(base + 1) * 256 + read(base + 2),
            "max_hp": read(base + 15) * 256 + read(base + 16),
            "level": read(base + 14),
            "status": StatusCondition(read(base + 4)).get_status_name(),
            "moves": [
                {"index": i, "name": name(Move, read(base + 8 + i)), "pp": read(base + 25 + i)}
                for i in range(4)
                if read(base + 8 + i)
            ],
        }

    def observe(self) -> dict:
        """Return a fresh snapshot without giving the model the global world graph."""
        location = dict(self.gs.map)
        name = self.navigation.graph.map_name(location["map_id"])
        location["map_key"] = name
        menu = self.battle.menu_state()
        in_battle = bool(location["in_battle"])
        service_menu = None if in_battle else self.overworld.observe()
        tutorial = in_battle and self.gs.mem.read_byte(0xD05A) == 1
        if in_battle:
            decision = (
                "switch"
                if self.battle.needs_switch()
                else (
                    "battle"
                    if menu == "main"
                    else "advance"
                    if menu in ("text", "nickname")
                    else "menu"
                )
            )
        else:
            decision = "advance" if self.gs.dialog.strip() else "overworld"
        allowed = {
            "switch": ["switch"],
            "battle": ["fight", "switch", "use_item", "run"],
            "advance": ["advance"],
            "overworld": ["navigate", "interact"],
            "menu": ["cancel"]
            if menu in ("moves", "bag", "party", "party_actions", "item_moves")
            else [],
        }[decision]
        if tutorial:
            decision, allowed = "advance", ["advance"]
        elif service_menu:
            decision = "menu"
            allowed = (
                ["quantity", "cancel"]
                if service_menu["kind"] == "quantity"
                else ["choose", "cancel"]
                if service_menu["options"]
                else []
            )
        if decision == "overworld" and self.pending_destination:
            allowed = [*allowed, "resume"]
        party = []
        for index, member in enumerate(self.gs.party_pokemon):
            hp, maximum = map(int, member["current_hp"].split("/"))
            party.append(
                {
                    "index": index,
                    "species": member["species_name"],
                    "nickname": member["nickname"],
                    "level": member["level"],
                    "hp": hp,
                    "max_hp": maximum,
                    "status": member["status"],
                    "types": [t for t in (member["type1"], member["type2"]) if t],
                    "moves": [
                        {"index": i, "name": move, "pp": pp}
                        for i, (move, pp) in enumerate(member["moves, pp"])
                    ],
                }
            )
        return {
            "schema_version": 1,
            "location": location,
            "exits": self._exits(name) if name else [],
            "objects": [{**obj, "name": object_name(name, obj)} for obj in self.gs.map_objects]
            if not in_battle
            else [],
            "party": party,
            "inventory": [
                {"index": i, "name": name, "quantity": quantity}
                for i, (name, quantity) in enumerate(self.gs.items)
            ],
            "money": self.gs.money,
            "badges": list(self.gs.badges),
            "battle": {
                "active": in_battle,
                "menu": menu,
                "kind": "tutorial"
                if tutorial
                else "wild"
                if self.battle.is_wild_battle()
                else ("trainer" if in_battle else None),
                "active_party_index": self.battle.active_mon_slot() if in_battle else None,
                "switch_options": self.battle.available_switches() if in_battle else [],
                "player": self._combatant(0xD014) if in_battle else None,
                "opponent": self._combatant(0xCFE5) if in_battle else None,
            },
            "menu": service_menu,
            "dialogue": self.gs.dialog,
            "decision": decision,
            "actions": allowed,
            "pending_destination": self.pending_destination,
        }

    def execute(self, request: dict) -> dict:
        """Validate and dispatch one action, returning its outcome plus fresh observation.

        submitted means inputs were sent, not a successful attack/escape. Use
        advance to reach the next decision. Unexpected bugs and EmulatorClosed
        propagate to the owner rather than becoming retryable model errors.
        """
        status, detail = "rejected", None
        try:
            if not isinstance(request, dict) or type(request.get("action")) is not str:
                raise ValueError("Request must be an object with an action string")
            action = request["action"]
            if action not in _ARGUMENTS:
                raise ValueError(f"Unknown action: {action}")
            required, optional = _ARGUMENTS[action]
            if not set(required) <= request.keys() or request.keys() - {
                "action",
                *required,
                *optional,
            }:
                raise ValueError("Missing or unexpected action arguments")
            arguments = {key: value for key, value in request.items() if key != "action"}
            for key, value in arguments.items():
                kind = (required | optional)[key]
                if type(value) is not kind or (kind is int and value < 0):
                    raise ValueError(f"Invalid {key}")
            observation = self.observe()
            if action not in observation["actions"]:
                raise ValueError(f"Action {action} is unavailable during {observation['decision']}")
            if action in ("navigate", "resume"):
                destination = (
                    arguments["destination"].upper()
                    if action == "navigate"
                    else self.pending_destination
                )
                if self.navigation.graph.map_id(destination) is None:
                    raise ValueError(f"Unknown map: {destination}; use a canonical map name")
                self.pending_destination = destination
                success = self.navigation.go_to(destination)
                status = "completed" if success else "blocked"
                detail = None if success else self.navigation.last_error
                if success:
                    self.pending_destination = None
            elif action == "interact":
                success = self.navigation.interact(**arguments)
                status = "completed" if success else "blocked"
                detail = "Dialogue started" if success else self.navigation.last_error
            elif action in ("choose", "quantity") or (
                action == "cancel" and not observation["battle"]["active"]
            ):
                success = getattr(self.overworld, action)(**arguments)
                status = "completed" if success else "blocked"
            elif action == "advance":
                success = (
                    self.battle.wait_for_turn(timeout=6000)
                    if self.battle.is_in_battle() and observation["battle"]["kind"] != "tutorial"
                    else self.overworld.advance()
                )
                # Ending combat is also a meaningful transition, not a timeout.
                status = (
                    "completed"
                    if success
                    or (observation["battle"]["active"] and not self.battle.is_in_battle())
                    else "timeout"
                )
            else:
                success = getattr(self.battle, action)(**arguments)
                detail = self.battle.last_result
                status = (
                    ("submitted" if action in ("fight", "run") else "completed")
                    if success
                    else ("timeout" if detail == "timeout" else "blocked")
                )
        except BattleInterrupt:
            status, detail = "interrupted", "Battle started; handle it before continuing"
        except ValueError as error:
            detail = str(error)
            status = "rejected"
        except (TimeoutError, RuntimeError) as error:
            status, detail = "failed", str(error)
        return {"status": status, "detail": detail, "observation": deepcopy(self.observe())}
