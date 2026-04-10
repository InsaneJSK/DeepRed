import sys
from pyboy import PyBoy
from pyboy.utils import WindowEvent
from memory_state.game_state import PokemonGameState

pyboy = PyBoy('Pokemon_Red/Red.gb', window='null')
pyboy.tick()
pyboy.load_state(open('saves/oak-room-battle.state', 'rb'))
gs = PokemonGameState(pyboy)

print("Starting to wait for battle...")
for ticks in range(3000):
    pyboy.tick()
    if ticks % 20 == 0:
        pyboy.send_input(WindowEvent.PRESS_BUTTON_A)
        pyboy.tick()
        pyboy.send_input(WindowEvent.RELEASE_BUTTON_A)
    
    if 'FIGHT' in gs.dialog and 'PKMN' in gs.dialog:
        print(f"Reached battle menu at tick {ticks}")
        print("====== DIALOG ======")
        print(repr(gs.dialog))
        print("====== DICT ======")
        import json
        try:
            print(json.dumps(gs.to_dict(), indent=2))
        except Exception as e:
            print(f"Error in to_dict: {e}")
        break
else:
    print("Timeout")

pyboy.stop()
