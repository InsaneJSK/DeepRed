# Usage and development

[Project overview and demo](../README.md) · [Navigation design](../NAVIGATION.md) · [Data provenance](../game_data/README.md)

## AI runner

After the README setup:

```powershell
uv run python -X utf8 main.py --save Pokemon_Red/Red.gb.state
```

`main.py` launches `ai_demo.py`; both expose the same options. Inference uses Ollama, defaulting to `http://localhost:11434`. The game pauses while waiting for a response. The Tk/Pillow display is for viewers and is not sent to the model.

| Option | Effect |
| --- | --- |
| `--model gemma3:4b` | Select an installed Ollama model; this is the CLI default |
| `--url http://localhost:11434` | Ollama server address |
| `--save PATH` / `--rom PATH` | Override local checkpoint / ROM paths |
| `--auto-flee` | Up to two automatic escape attempts per wild encounter; off by default |
| `--headless` | Run without a window, at unlimited emulator speed |
| `--no-overlay` | Use the plain PyBoy SDL2 window instead of the decision panel |
| `--max-calls 30` / `--max-actions 200` | Bound model requests / all action attempts |
| `--timeout 180` | HTTP request timeout in seconds |
| `--stop-at MAP` / `--no-stop-at` | Change / disable the local arrival stop |
| `--no-auto-resume` | Let the model decide when to resume interrupted travel |
| `--think` | Enable reasoning on supported Ollama models; off by default |
| `--verbose` | Print controller diagnostics as well as model choices |

The goal is currently fixed in `PLAY_INSTRUCTION` and `SYSTEM_PROMPT` in [llm_runner.py](../autonomous_controller/llm_runner.py). It explicitly directs travel to Viridian City. Changing `--stop-at` changes the arrival check, not the model's goal. Keep the goal and finish guidance consistent if editing the prompt. Restart the demo to load changes.

`--auto-flee` runs only on actionable wild-battle turns. Trainer battles, tutorials, forced switches and other menus remain outside that policy. After two attempts in an encounter that remains active, the model receives control and the attempt history. Counters reset when a returned observation shows combat has ended. “Auto: attempt escape” and **AUTO POLICY** distinguish controller decisions from model choices.

## Human CLI

The manual demo uses the same action interface:

```powershell
uv run python -X utf8 demo.py --save Pokemon_Red/Red.gb.state --no-headless --auto-advance
```

The game pauses while you type. `--auto-advance` handles compulsory text but stops at decisions; without it, use `advance` when listed.

| Command | Purpose |
| --- | --- |
| `navigate VIRIDIAN_CITY` | Request a final destination across maps |
| `interact 1` | Approach and interact with the printed NPC/object slot |
| `choose_starter squirtle` | Choose a starter when offered |
| `fight 0` / `switch 1` | Select a move / healthy reserve party member |
| `use_item 2 0` | Use bag item 2 on party member 0, where supported |
| `use_item 0` | Use an item that needs no target, such as a supported ball |
| `choose 0` / `quantity 3` | Select a menu option / purchase quantity |
| `run` / `cancel` | Attempt escape / leave an optional menu |
| `advance` / `resume` | Advance text / resume interrupted travel |
| `observe` / `json` / `help` | Inspect state / full observation / command help |
| `/save` / `/save saves/example.state` | Create a checkpoint |
| `quit` | Exit without another save |

Move, party, bag and menu indices start at zero. NPC slots use their printed numbers. Shared sprites get role labels rather than invented identities. Interaction can approach NPCs across supported counters.

## Saves and logs

Manual `/save` refuses to overwrite an existing file. Load checkpoints with `--save` in either runner. Saves preserve emulator state, including menus and battles, but not model memory, pending navigation destinations, or run options. The next run replans from its observation.

AI runs create an ignored `status/llm-runs/<timestamp>/` directory:

- `events.jsonl`: prompts, responses, action results, policy settings and final summary.
- `controller.log`: diagnostic output hidden from the clean display.
- `summary.json`: model calls by decision type, automatic action counts, token usage and timings. On interruption, consult the summary event in `events.jsonl`.
- `checkpoint-*.state`: checkpoints every ten actions and on exit.

Counts include failed requests, invalid responses and model `finish` responses. Automatic actions do not count as model calls. Token usage is reported by Ollama; missing usage is tracked separately. There are no hidden HTTP retries. Edited video duration is not an inference benchmark.

## Memory, limits and result meanings

Each request includes current state, up to six recent model outcomes or automatic failures/escape attempts, observed map-entry counts, and up to eight notable dialogue/interactions/state-change records. Successful text advances and route resumes do not evict model choices. Memory is local to the run.

`AgentMemory.TRIP_LIMIT` currently equals `1`: after `X → Y → X` without new observations, another `X → Y` request is rejected before movement. New places, new dialogue, first NPC interactions, or changes in party/inventory/money/badges reset the counter. These are heuristics, not a complete quest-state model; memory tracks returned observations, not every internal navigation frame.

Three consecutive action failures/rejections stop the run. Eight consecutive automatic actions with unchanged before/after observations stop with `automatic_no_progress`. Successful automatic sequences can exceed eight actions, subject to the total action budget. Model calls and total actions have separate limits.

`submitted` means inputs were sent; it does not establish that a move landed or escape succeeded. `completed` describes the controller operation, not overall goal progress. `destination_reached` is verified from the observed map outside battle/dialogue. `model_finished` is a model claim to check against the trace.

## Testing

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Without game assets:

```powershell
uv run pytest -m "not integration and not source_data"
```

Tests group related behavior and use subtests for variants. They cover action validation, routing, move/menu selection, memory and repetition limits, shutdown, transport, and automatic policies. Real-emulator tests check effects such as PP consumption and battle termination rather than merely trusting controller return values. Some menu fixtures deliberately modify in-memory test state to isolate switching, items and capture; they do not overwrite player saves.

The optional escape regression uses `saves/wild-escape.state`, a known wild-battle checkpoint that allows escape within two attempts. Set `DEEPRED_WILD_STATES` to multiple such paths separated by `;` on Windows to replay additional fixtures. Missing game assets cause integration tests to skip. The `source_data` marker checks importer parity when the pinned source checkout is present; it is not a runtime requirement.

The scripted route diagnostic is separate from the LLM runner:

```powershell
uv run python -X utf8 scratch/navigation_regression.py --state saves/in-room-start.state --goals ROUTE_1 VIRIDIAN_CITY
```

It uses a fixed battle policy, so it is a controller regression, not an AI performance test. `--checkpoint` writes ignored intermediate states under `scratch/`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Missing default checkpoint | Fresh clones do not contain saves. Create one and pass `--save` explicitly. |
| ROM checksum mismatch | Supply the supported revision; RAM addresses and terrain are revision-specific. |
| Ollama connection/model error | Start Ollama, pull the selected model, and check `--url` / `--model`. |
| Tk display unavailable | Use `--no-overlay` for SDL2 or `--headless` for no window. |
| Wait at startup | The current visible launcher has a 10-second recording delay before play begins. |
| Model repeats poor choices | Inspect the trace. Safeguards bound loops but cannot guarantee good decisions. |
| Auto-flee returns to fighting | Its two attempts were used without combat ending; the model now controls the encounter. |
| Window remains after arrival | The final frame is intentionally retained; close the window. |
| Test is skipped | Read the reason; ROM/saves and the optional upstream checkout are not distributed. |

Optional notebooks use `uv sync --locked --all-groups`. Session reports, run logs and generated game states stay in ignored directories. No automatic commit hooks are installed.
