from pyboy import PyBoy

pyboy = PyBoy("Pokemon_Red\\Red.gb", window="SDL2")

# with open("in-room-start.state", "rb") as f:
#     pyboy.load_state(f)
# Iteratively saved the state until player is in room

# Let emulator boot and player be in room
for _ in range(600): 
    pyboy.tick()

# Save state correctly
with open("saves\\in-room-start.state", "wb") as f:
    pyboy.save_state(f)

pyboy.stop()