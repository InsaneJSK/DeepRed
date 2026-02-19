"""
Defines the gamewrapper from in-built PyBoy, which is used to interact with the game.
"""

from pyboy import PyBoy
pyboy = PyBoy("Pokemon_Red/Red.gb", window="SDL2")
with open("saves\\in-room-start.state", "rb") as f:
    pyboy.load_state(f)
game = pyboy.game_wrapper
print(game.enabled)
