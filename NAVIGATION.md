# Navigation and controller design

[Overview and demo](README.md) · [Usage guide](docs/USAGE.md) · [Data provenance](game_data/README.md)

DeepRed separates choosing a destination from executing the trip. The model can request `navigate VIRIDIAN_CITY` from Red's bedroom; it does not need to choose each door, intermediate map or button press. The recorded demo verifies an early-game journey through starter selection, the rival battle and wild encounters. It does not establish general late-game routing.

## Two sources of knowledge

**Static terrain:** [game_data/pokemon_red.json](game_data/pokemon_red.json) contains map IDs, dimensions, tile geometry, collision rules, directional tile-pair restrictions, warps and overworld connections. A pinned source revision, supported ROM checksum and payload digest identify the bundle. Runtime validates its structure and integrity.

**Live state:** [memory_state/game_state.py](memory_state/game_state.py) reads player coordinates, facing, map identity, dialogue and current-map object records from emulator RAM. Visible NPC occupancy is refreshed during navigation, including occupied/reserved squares while sprites move.

Full-map terrain solves the original viewport limitation for static walls and paths. It does not supply live offscreen NPC positions or all story-driven terrain changes. Offscreen records are not treated as continuously simulated occupancy.

## Planning and execution

1. [WorldGraph.terrain_route](autonomous_controller/world_graph.py) searches over connected walking regions, not only map IDs. A gate on another inaccessible part of the same map is not automatically reachable.
2. [NavAstar](autonomous_controller/nav_astar.py) and [pathfinder.py](autonomous_controller/pathfinder.py) plan within map bounds using walkability, directional restrictions, visible occupancy and bounded search.
3. [HopExecutor](autonomous_controller/hop_executor.py) executes map transitions. Doors and boundary connections are distinct: overworld connections use shared border squares and offsets, then verify the destination map.
4. [AutonomousController](autonomous_controller/controller.py) coordinates the route. It checks actual movement, replans around failed edges, waits within limits for blockers, and reports unexpected transitions or script displacement.

Warp approaches avoid unrelated warp squares. A map change or scripted displacement invalidates the current local plan. Story gates are not assumed passable just because the maps are connected.

## Interruptions and action results

Travel may raise a battle or starter-choice interruption. [AgentInterface](autonomous_controller/agent_interface.py) converts this into a structured result, exposes the next available decisions, and keeps a pending destination. The model chooses the starter and supported battle/menu actions; the runner can resume navigation once those decisions are resolved.

The human CLI and LLM runner share that interface. `observe()` reads state without advancing emulation; `execute()` validates arguments and availability, invokes a controller, and returns a fresh observation.

| Result | Meaning |
| --- | --- |
| `completed` | The requested controller operation completed |
| `submitted` | Inputs were sent; a later observation must establish the outcome |
| `interrupted` | Another decision, such as battle or starter selection, needs handling |
| `blocked`, `timeout`, `failed` | Execution could not complete; inspect the detail |
| `rejected` | The request was not accepted; the runner can also reject repeated travel before execution |

For example, selecting RUN is not proof of escape. The game must leave battle, and successful escape regression tests also check the game message and unchanged move PP.

## Bounds at different levels

- Movement and dialogue controllers use finite search/frame budgets and release pressed buttons on interruption.
- Action validation rejects malformed requests and unsupported choices.
- The LLM runner limits requests and total action attempts, retains a compact memory, and rejects repeated directed trips without new observations.
- Optional auto-flee allows at most two controller-selected escape attempts per observed wild encounter; it is off by default and labelled separately from model decisions.

These bounds prevent a model or controller from retrying indefinitely. They do not prove that every accepted action advances the story.

## Verification and limitations

Grouped tests cover terrain bounds, path search, dynamic blocking, region-aware routing, connection offsets, interruptions, menus, action validation and shutdown. Local real-emulator checks exercise early routes and battle effects. Some test fixtures seed RAM to isolate a specific menu branch; ordinary gameplay uses button inputs.

Static terrain and the bundled graph do not provide generalized Surf, Cut, Strength, ledge-jump, warp-puzzle or late-game script planning. Event-driven terrain changes and certain special return destinations remain limited. See the explicitly unsupported map and regeneration constraints in [game_data/README.md](game_data/README.md).

No external pokered checkout or generated `world_graph.json` is required at runtime. [build_world_graph.py](autonomous_controller/build_world_graph.py) remains an offline exporter helper. To reproduce scripted controller journeys, use the command in the [testing guide](docs/USAGE.md#testing); it is distinct from the LLM demo.
