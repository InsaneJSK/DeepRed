# Navigation rewrite — 8 September 2026

Navigation now reaches Route 1 and Viridian City through real overworld
connections. The original room save was exercised through Oak's sequence,
starter selection, the rival battle, wild encounters, and both north crossings.

## Major changes

- Replaced viewport-based guesses and repeated dodging with a full-map walking
  grid decoded from the matching pokered `.blk`, `.bst`, and collision lists.
  Public terrain coordinates now use 16-pixel player steps consistently.
- A* uses real map bounds, directional tile-pair restrictions, parent pointers,
  and an actual node-expansion limit. Blocked goals fail explicitly; they are
  not silently replaced by neighboring squares.
- Live sprite records are available as `PokemonGameState.map_objects` and in
  `to_dict()`. Navigation avoids visible objects and reserves both squares during
  an NPC's walking animation. Off-screen records are not assumed to be continuously
  simulated occupancy. Terrain is available for the whole map independently.
- Failed moves exclude the attempted edge for replanning. Dynamic blockers get
  a bounded wait. The old straight-line goal cache and blind dodge/escape loops
  are no longer used. Existing `path_cache.json` is left untouched.
- Connections examine both maps' shared walking squares, including odd
  coordinates and the signed connection offset. They choose a reachable border,
  take a full step across it, and verify the actual destination map.
- Warp execution avoids unrelated warp squares, tries reachable approaches, and
  handles doors that activate when stepping out of the doorway. Movement stops
  immediately when a map change or scripted displacement invalidates the plan.
- World routing distinguishes separate walking regions within a map. From the
  south of Route 2 it selects the southern forest gate, not the inaccessible
  northern entrance merely because it shares the Route 2 map ID.
- Dialogue gets a longer settling window. `BattleInterrupt` now propagates to
  the caller, including battles at the destination. The example travel loop flees
  wild encounters, fights trainers, and resumes navigation. An unchanged route
  failure stops the loop rather than retrying it repeatedly.
- Fixed party nickname reads that incorrectly requested roughly 54,000 bytes
  per Pokémon instead of the 11-byte name field.

High-level calls remain `go_to(MAP_NAME)`, `navigate_to_tile(x, y)`, and
`pick_starter(name)`. `go_to` returns a bool for arrival/failure and raises
`BattleInterrupt` for combat. `last_error` explains a failed hop;
`nav_stats` counts movement calls and blocked steps. Terrain helper internals
changed substantially; old block-coordinate helpers are no longer supported.

## Verified results

All checks used Python 3.11.9, PyBoy 2.7.0, the existing ROM, and real button
inputs. No position, party, story-flag, or battle-memory writes were used.
Original saves and cartridge RAM were not saved over. Checkpoints are confined
to ignored `scratch/*.state` files.

| Check | Result |
|---|---|
| Original bedroom → Route 1 → Viridian City | Passed; 128 movement calls, 11 blocked attempts including scripted sequences, 18,089 emulated frames in the recorded run |
| Route 1 checkpoint → Viridian City | Passed; 55 movement calls, zero blocked steps, three escaped wild encounters |
| Viridian → Route 22 → Viridian → Pallet | Passed; 134 movement calls, zero blocked steps; verifies west/east/south crossings and offsets |
| Viridian → forest with the current story state | Stopped at the old-man gate after 28 movement calls and zero blocked steps; reported scripted displacement |
| Automated regression suite | 12 tests passed: dimensions, floor/furniture, bounds, detours, reverse connection offsets, forest entrance selection, empty/blocked paths, search budget, forbidden tiles, live object occupancy, and bounded waits |

The run counts describe specific save states, not timing guarantees. Encounters
and NPC movements can change with input timing. `scratch/navigation_final.log`,
`navigation_city.log`, `navigation_return.log`, and `navigation_gate.log` contain
the recorded emulator output; logs are ignored by Git.

## Run and verify

Run from the repository root with uv (see README.md for setup):

```powershell
uv sync --locked
uv run python -X utf8 main.py
uv run python -X utf8 -m unittest discover -s tests -v
uv run python -X utf8 scratch/navigation_regression.py
```

`main.py` still defaults to `ROUTE_1`; set `GOAL = "VIRIDIAN_CITY"` to demonstrate
both north crossings. The headless regression script defaults to both goals.
Use `--checkpoint` to keep intermediate test states in scratch. The tests require
the local pokered checkout and generated `world_graph.json`.

## Remaining scope

- The current story state requires completing Oak's parcel/Pokédex sequence
  before the northern Viridian route opens. Navigation respects that gate; it
  does not automate the quest. End-to-end forest travel is not claimed.
- This is a walking planner. It does not plan Surf, Cut, Strength, ledge jumps,
  warp-pad puzzles, or every later-game script. Avoiding jumps can produce a
  longer but walkable return route.
- Full-map terrain comes from the matching disassembly, not a live snapshot of
  every event-modified block. Dynamic terrain changes need additional support.
- The existing world-graph builder still guesses some ambiguous LAST_MAP exits.
  Runtime transitions are checked, but arbitrary late-game graph routing is not
  validated. Future work should preserve/resolve these exits using live context.
- Visible NPC occupancy is refreshed on each planning step; it cannot promise
  an NPC will not move into the next square. Such failures trigger bounded
  replanning rather than permanent terrain changes.
