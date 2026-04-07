from pyboy import PyBoy

from memory_state.game_state import PokemonGameState
from autonomous_controller.controller import AutonomousController
import json
g = json.load(open('world_graph.json'))

# # Trace the expected path manually
# for name in ['REDS_HOUSE_2F', 'REDS_HOUSE_1F', 'PALLET_TOWN']:
#     entry = g['maps'].get(name, {})
#     print(f"\n{name}:")
#     print(f"  warps: {entry.get('warps')}")
#     print(f"  connections: {entry.get('connections')}")

state = PyBoy("Pokemon_Red/Red.gb", window="SDL2")
with open("saves\\in-room-start.state", "rb") as f:
    state.load_state(f)

gs = PokemonGameState(state)
controller = AutonomousController(state, gs, "world_graph.json")

# # Tick a few frames first to let the save state fully initialize
# for _ in range(60):
#     state.tick()

# mem = gs.mem
# warp_count = mem.read_byte(0xD36A)

# target_map_id = 37  # REDS_HOUSE_1F = 0x25
# print(f"Scanning for warp to map_id={target_map_id} ({target_map_id:#04x})")
# print(f"Current map: {mem.read_byte(0xD35E)}, size: {mem.read_byte(0xD368)}x{mem.read_byte(0xD369)}")
# print()

# # Scan a wide range around the expected warp table area
# for base in range(0xD300, 0xD500):
#     val = mem.read_byte(base)
#     if val == target_map_id:
#         # Check if surrounding bytes look like a warp entry (y, x, dest_warp, dest_map)
#         surrounding = [mem.read_byte(base + i) for i in range(-3, 4)]
#         print(f"  Found map_id {target_map_id} at {base:#06x}: context = {surrounding}")

# # Dump the memory region around where warps should be
# # and also check neighboring addresses for the warp count
# print(f"Player pos from game_state: x={gs.map['player_x']}, y={gs.map['player_y']}")
# print(f"Map ID: {mem.read_byte(0xD35E)}")
# print()

# # Check candidate warp count addresses
# for addr in [0xD36A, 0xD36B, 0xD36C, 0xD369, 0xD368]:
#     val = mem.read_byte(addr)
#     print(f"  {addr:#06x} = {val}")

# print()
# # Dump 32 bytes starting from a few candidate bases
# for base in [0xD36B, 0xD36A, 0xD369]:
#     print(f"  Dump from {base:#06x}:")
#     row = [mem.read_byte(base + i) for i in range(32)]
#     print("  ", row)

# print(f"Warps on current map: {warp_count}")
# for i in range(warp_count):
#     base = 0xD36B + i * 4
#     wy = mem.read_byte(base)
#     wx = mem.read_byte(base + 1)
#     dest_warp = mem.read_byte(base + 2)
#     dest_map  = mem.read_byte(base + 3)
#     print(f"  Warp {i}: RAM says x={wx}, y={wy} → map_id={dest_map}, dest_warp={dest_warp}")

# # The entry [y=1, x=7, dest_warp=2, dest_map=37] starts at 0xD3AF
# # Dump backwards to find the count byte
# print("Dump around real warp table:")
# for i in range(0xD3A8, 0xD3C0):
#     print(f"  {i:#06x} = {mem.read_byte(i)}")

# px, py = gs.map["player_x"], gs.map["player_y"]
# print(f"Player is at: x={px}, y={py}")

controller.go_to("VIRIDIAN_CITY")