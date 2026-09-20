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
