# DeepRed

Autonomous Pokemon Red navigation and gameplay using PyBoy and structured RAM
observations. See [NAVIGATION.md](NAVIGATION.md) for the implementation and its
verified routes and limitations.

## Setup with uv

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if needed,
then open PowerShell in this repository:

```powershell
uv sync --locked
```

uv manages Python and the `.venv` environment. No manual environment activation
is needed. `.python-version` selects Python 3.11.9; `pyproject.toml` declares
dependencies and `uv.lock` records their exact resolved versions. Commit all
three files. The emulator dependencies preserve the tested pre-migration versions.

The following local assets are also required; uv does not download game assets:

- `Pokemon_Red/Red.gb`: the matching Pokemon Red ROM.
- `saves/in-room-start.state`: the existing initial emulator save.

Navigation loads the checked-in `game_data/pokemon_red.json` bundle. No pokered
checkout or separately generated world_graph.json is needed to play or run the
normal navigation tests. The supported ROM SHA-1 is
`ea9bcae617fdf159b045185467ae58b2e4a48b9a`; the entry point checks it before booting.
See [game_data/README.md](game_data/README.md) for provenance and optional updates.

## Run and test

Watch the emulator navigate from the bedroom through Route 1 to Viridian City:

```powershell
uv run python -X utf8 main.py
```

Change `GOAL` in `main.py` to choose another destination.
Close the emulator window or press Ctrl+C in the terminal to exit, including
during navigation or battles. Shutdown releases the emulator without overwriting
the original cartridge RAM file.

Run the test suite and, separately, the full headless bedroom-to-Viridian regression:

```powershell
uv run pytest
uv run python -X utf8 scratch/navigation_regression.py
```

Pytest discovers tests in `tests/`, including the existing unittest tests. Tests
marked `integration` require a real emulator and local game assets; missing
required assets are reported as skips, not passes. Run the fast subset with
`uv run pytest -m "not integration and not source_data"`, or only integration tests with
`uv run pytest -m integration`. The full travel regression above is a separate
script and is not included in pytest discovery. The optional `source_data` check
verifies bundle regeneration against pokered and skips when that checkout is absent.

The headless travel regression leaves original saves and cartridge RAM unchanged. Add
`--checkpoint` to retain test checkpoints in `scratch/`.

An expected story-gate failure must specify the exact map, position, and reason;
an unrelated navigation failure will fail the regression:

```powershell
uv run python -X utf8 scratch/navigation_regression.py --goals VIRIDIAN_FOREST --expect-blocked --blocked-map VIRIDIAN_CITY --blocked-position 19 10 --blocked-reason "A game script moved or stopped the player."
```

To create a separate manual-testing checkpoint, provide an input and a new
output filename. Existing files are never overwritten:

```powershell
uv run python -m autonomous_controller.save_state --input saves/in-room-start.state --output saves/new-checkpoint.state --frames 600
```

Manual emulator helpers now honor window closure and do not write cartridge RAM
on exit. Closing before checkpoint creation cancels the operation.

## Development

### Agent interface

Try the human-controlled CLI from the project folder:

```powershell
uv run python demo.py
# Watch the game and automatically advance mandatory dialogue/animation:
uv run python demo.py --no-headless --auto-advance
# Or use another existing checkpoint:
uv run python demo.py --save saves/oak-room-battle.state
```

The default is headless with manual advancement. `--no-headless` opens a game
window at normal speed; `--headless` runs without it at unlimited speed.
`--auto-advance` automatically executes `advance` only when it is the sole
available action. It stops at a choice, unsuccessful result, or eight consecutive
automatic advances. `--no-auto-advance` keeps advancement manual. The game pauses
while you type, and the visible window continues processing close events.
It starts from the bedroom save by default, displays current state and
available actions, and accepts commands such as `navigate REDS_HOUSE_1F`,
`interact 5`, `fight 0`, `switch 1`, `use_item 2 0`, `advance`, and `resume`.
Party/bag/move indices are zero-based; object slots use the numbers printed by
the CLI. Use `help` for syntax, `json` for the complete observation, and
`quit` or Ctrl+C to exit without saving. Each action uses the same interface
below; no separate human-only gameplay logic is introduced.

Type `/save` at any CLI prompt to create a timestamped emulator checkpoint in
`saves/`, or `/save saves/my-checkpoint.state` to choose a filename. Paths with
spaces can be quoted. Existing files are not overwritten. Saving keeps the demo
open and prints the path and a command to reload it:

```powershell
uv run python demo.py --save "saves/my-checkpoint.state" --no-headless --auto-advance
```

Checkpoints preserve the game, including battles and menus. On a new run, choose
your window/auto-advance options again and reissue any interrupted travel goal.
`quit` does not save again or remove checkpoints you already created.

`AgentInterface` exposes JSON-compatible observations and validated actions to a
future model adapter. It uses an already loaded emulator session and does not
own its lifetime or call a model:

```python
from autonomous_controller.agent_interface import AgentInterface

agent = AgentInterface(session, game_state)
observation = agent.observe()  # does not advance the emulator
schema = agent.action_schema()  # portable JSON Schema for action requests
result = agent.execute({"action": "navigate", "destination": "ROUTE_1"})
```

Choose actions from `observation["actions"]`. `navigate` accepts any known map
as the final destination, including maps beyond the current map's `exits`.
For example, `navigate ROUTE_1` from `REDS_HOUSE_2F` handles the stairs, front
door and Pallet Town automatically. Accessibility is unverified until attempted;
the observation does not expose the global map graph. Results include `status`,
`detail`, and an updated `observation`. `submitted` means input was sent, not
that an attack or escape succeeded. Use `advance` to reach the next decision.

Available request names are `navigate`, `interact`, `resume`, `fight`, `switch`, `use_item`,
`run`, `cancel`, `advance`, `choose`, and `quantity`. Their arguments are described by `action_schema()`;
party, move, and bag indices are zero-based. Battle interrupts retain the travel
destination; after combat and dialogue, `resume` continues that request. A new
navigation request replaces it. The existing starter-selection fallback remains
part of navigation. Unsupported decision menus expose no available action.

### Local Ollama runner

Start Ollama with a locally installed model, then run:

```powershell
uv run python ai_demo.py --model qwen3:4b --goal "Reach ROUTE_1" --stop-at ROUTE_1 --max-calls 30 --no-headless
# Start from a manual checkpoint:
uv run python ai_demo.py --model qwen3:4b --save saves/after-healing.state --goal "Visit VIRIDIAN_MART and buy 3 Poke Balls" --max-calls 20
```

`--url` defaults to `http://localhost:11434`; `--model` defaults to `qwen3:4b`.
Headless is the default. `--think` enables reasoning on supported models; it is
off by default. The runner uses Ollama's native `/api/chat` with structured JSON
and validates every gameplay action through the same interface as the CLI.

Each real choice gets one model call: destinations, interactions, menus and
battle turns. Mandatory text/animations advance automatically. After a battle,
the pending route resumes automatically unless `--no-auto-resume` is set.
A blocked automatic resume returns control to the model; it is not retried in
a controller loop. Story events and the existing automatic starter selection
remain controller behavior. The model never receives screenshots or the global
route graph, and never needs to supply an intermediate route.

Each run writes `events.jsonl`, `summary.json`, and checkpoints under the ignored
`status/llm-runs/<timestamp>/` directory. The summary counts HTTP attempts
(including failed calls and the final model `finish` decision), calls by decision
type, automatic actions, model latency and backend-reported token usage.
Missing token usage is tracked via `responses_with_usage`; totals cover only
responses that supplied counts. These measurements are not a cloud cost quote.
Only the last six action outcomes accompany the current observation, bounding
history growth. Complete prompts, responses and action results remain in the log.

The run stops at `--max-calls` (default 30), `--max-actions` (default 200), repeated
failures, three consecutive model actions with unchanged observations, unsupported
menus, or when the model says `finish`. For destination-only goals, `--stop-at MAP`
stops on observed arrival outside battle/dialogue, avoiding a completion call. Model completion
is a claim to inspect in the log, not an independently verified success.
Ctrl+C or closing the game window also stops execution. Checkpoints are saved
every ten executed actions and on exit; open them with either demo's `--save`.
On an interrupted run, the summary is in the final event in `events.jsonl`.

`interact SLOT` approaches a current-map sprite, faces it, and starts dialogue.
It leaves the first text page visible; use `advance` to continue. Object slots
are local to each map, and sprites can be people, items, or other objects. An
off-screen/hidden entry is only confirmed as a target after approaching it.
For example, after returning to Oak's lab with the parcel, `interact 5` talks to
Oak and `advance` handles the delivery. Interaction supports adjacent targets and
talking across tiles identified by the game as counters. Objects show readable
sprite roles/names while retaining their map-local slot for `interact`.

Overworld menus expose named options with zero-based indices. Use `choose 0`
for the first displayed option, or `quantity 3` at a shop's quantity prompt.
`advance` (including auto-advance) stops at these choices. For example, after
delivering the parcel, talk to the Mart clerk, choose Buy, choose Poke Ball,
set the quantity, then choose Yes to confirm payment. Use `advance` whenever
it is the only available action, or enable `--auto-advance`.

At a Pokemon Center, interact with the nurse, advance to HEAL/CANCEL, and
choose Heal. In Viridian City, the old man's optional catching tutorial is
available after parcel delivery: talk to him and answer No to "Are you in a
hurry?" The scripted demonstration advances without offering player fight actions.

Window closure propagates `EmulatorClosed` to the session owner. Keep the usual
`finally: emulator.stop(save=False)` cleanup. The existing `main.py` demo still
uses its fixed policy; the interface does not introduce a model dependency.

The default `dev` group includes Ruff and pytest. Ruff checks basic Python errors
and import ordering, and formats Python code with a 100-character target width.
Configuration lives in `pyproject.toml`; external repositories, local game assets,
reports, and notebooks are excluded from Ruff.

Check before committing (these commands do not edit source files):

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Preview changes before applying them:

```powershell
uv run ruff check . --diff
uv run ruff format . --diff
```

Apply safe lint fixes and formatting when ready. Replace `.` with a filename to
work on one file. Some lint findings need manual edits.

```powershell
uv run ruff check . --fix
uv run ruff format .
```

These tools run on demand; no automatic commit hooks are installed.
Notebook support is optional:

```powershell
uv sync --locked --all-groups
uv run --group notebooks python -m ipykernel --version
```

Select `.venv/Scripts/python.exe` as the interpreter in your editor. For notebooks,
enable the `notebooks` group and select that same environment as the kernel.

Use `uv add PACKAGE`, `uv add --dev PACKAGE`, and `uv remove PACKAGE` to change
dependencies. Run `uv lock --check` to check that the lockfile matches the project.
Dependency upgrades are separate from this migration; rerun navigation tests
after deliberately changing the emulator version pins.

`requirements.txt` has been replaced by `pyproject.toml` and `uv.lock`. If another
tool needs a requirements export, generate it rather than maintaining two lists:

```powershell
uv export --locked --no-dev --format requirements-txt --output-file requirements.txt
```

The old `venv/` directory has been removed; `.venv/` is the active uv environment.
Vendored repositories and non-Python projects keep their own dependency files.

## Migration validation (19 September 2026)

Verified `uv lock --check`, all 12 unit tests, optional notebook support, and the
full headless bedroom → Route 1 → Viridian City run in `.venv`. The emulator run
matched the previous result: 128 movement calls and 18,089 emulated frames.

The existing NumPy 2.4.0 pin is intentionally preserved for this migration.
uv reports that this release was yanked upstream for a backward-compatibility
bug; changing that baseline should be a separately tested dependency update.
