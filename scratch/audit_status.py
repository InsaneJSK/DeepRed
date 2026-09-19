"""Read-only emulator audit; never saves cartridge RAM or updates path_cache.json."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pyboy import PyBoy
from memory_state.game_state import PokemonGameState
from autonomous_controller.controller import AutonomousController


class BoundedEmulator:
    def __init__(self, emulator, limit=30000):
        self.emulator, self.limit, self.frames = emulator, limit, 0

    def __getattr__(self, name):
        return getattr(self.emulator, name)

    def tick(self, *args, **kwargs):
        self.frames += args[0] if args else 1
        if self.frames > self.limit:
            raise TimeoutError('Audit frame budget reached')
        return self.emulator.tick(*args, **kwargs)


for state in ['saves/in-room-start.state', 'saves/oak-room-battle.state', 'Pokemon_Red/Red.gb.state']:
    p = PyBoy(str(ROOT / 'Pokemon_Red/Red.gb'), window='null', sound_emulated=False)
    p.set_emulation_speed(0)
    try:
        with open(ROOT / state, 'rb') as f:
            p.load_state(f)
        p.tick(1)
        gs = PokemonGameState(p)
        sprites = []
        for slot in range(1, 16):
            a, b = 0xC100 + slot * 16, 0xC200 + slot * 16
            if p.memory[a]:
                sprites.append(dict(slot=slot, picture=p.memory[a], image=p.memory[a+2],
                                    x=p.memory[b+5]-4, y=p.memory[b+4]-4,
                                    movement=p.memory[a+1]))
        print('SNAPSHOT', json.dumps(dict(state=state, map=gs.map,
              dimensions_raw=[p.memory[0xD369], p.memory[0xD368]],
              sprites=sprites, party=[v.get('species_name') for v in gs.party_pokemon])), flush=True)
        if state == 'saves/in-room-start.state':
            wrapped = BoundedEmulator(p)
            c = AutonomousController(wrapped, gs, str(ROOT / 'world_graph.json'))
            c.path_cache = None
            c._debug = lambda *args: None
            print('GRAPH_ROUTE', c.graph.bfs_route(c._map_name(), 'VIRIDIAN_FOREST'), flush=True)
            try:
                print('NAV_RESULT', c.go_to('ROUTE_1'), flush=True)
            except Exception as exc:
                print('NAV_EXCEPTION', type(exc).__name__, str(exc), flush=True)
            print('NAV_FINAL', json.dumps(gs.map), 'frames', wrapped.frames, flush=True)
    finally:
        p.stop(save=False)
