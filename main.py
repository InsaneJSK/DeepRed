"""
This is the main entry point for the DeepRed project.
Currently, it is used to test the autonomous controller.
"""

import json

from pyboy import PyBoy

from memory_state.game_state import PokemonGameState
from autonomous_controller.controller import AutonomousController

with open('world_graph.json', 'r', encoding='utf-8') as f:
    g = json.load(f)

state = PyBoy("Pokemon_Red/Red.gb", window="SDL2")
with open("saves\\in-room-start.state", "rb") as f:
    state.load_state(f)

gs = PokemonGameState(state)
controller = AutonomousController(state, gs, "world_graph.json")

controller.go_to("VIRIDIAN_CITY")
