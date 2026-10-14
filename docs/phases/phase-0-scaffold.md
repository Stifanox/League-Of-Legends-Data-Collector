# Phase 0 — Scaffold, config, secrets, logging

You are implementing phase 0 of a rewrite of a Riot Games API crawler (League of Legends) in this repository.

## Read first
- `docs/DESIGN.md` — the agreed design. Sections 3, 4 and 13 matter most for this phase. Do not change decisions recorded there; if something is impossible or ambiguous, stop and ask.
- The current code (`main.py`, `constants/`, `data_collector/`, `model/`, `remote/`, `use_case/`, `utils/`) only for context — it will be deleted.

## Rules
- **No git write actions** (no branch, commit, push, stash, checkout, `git rm`). Delete files with normal file operations. The user commits.
- Python 3.13, venv in `.venv`. Match the style you set up here in all later phases: PEP 8, `snake_case`, type hints, short docstrings.

## Tasks
1. Delete the old code: `main.py`, `constants/`, `data_collector/`, `model/`, `remote/`, `use_case/`, `utils/` and all `__pycache__`/`.pyc` files. Leave `.idea/` untouched.
2. Create the layout:
   ```
   riot_client/__init__.py
   crawler/__init__.py
   crawler/__main__.py        # argparse CLI skeleton: crawl | export | retry-parked | status | ddragon (stubs printing "not implemented yet")
   crawler/config.py
   crawler/logging_setup.py
   tests/__init__.py (if needed), tests/conftest.py, tests/fixtures/
   docs/  (exists)
   ```
3. `requirements.txt` with pinned versions of: `httpx`, `python-dotenv`, `pyarrow`, `alive-progress`, `pytest`, `pytest-asyncio`. Install into `.venv` and pin what actually installed.
4. `.gitignore` per DESIGN §4.
5. `.env.example` with **every** variable from the DESIGN §4 table, defaults filled, `RIOT_API_KEY=` empty, a one-line comment per variable.
6. `crawler/config.py`:
   - frozen dataclass `Settings` with every variable from the §4 table (typed: lists, bools, ints, floats, optional date);
   - `load_settings(env_file: Path = Path(".env")) -> Settings` that (re)reads the file **every call** (`dotenv_values` + `os.environ` override). Phase 1 depends on re-reading for key swaps;
   - validation with clear error messages (missing key, unknown region, bad strategy, snowball without `SEED_RIOT_ID`, etc.);
   - platform → regional routing map from DESIGN §5.
7. `crawler/logging_setup.py`:
   - console handler (INFO, compact) + rotating file handler `LOG_DIR/crawler.log` (DEBUG, ~10 MB × 5);
   - a `logging.Filter` that masks `RGAPI-[0-9a-fA-F-]+` → `RGAPI-****` in message **and** args **and** exception text.
8. Tests:
   - settings parsing: defaults, list parsing, bool parsing, validation errors, re-reading picks up a changed file;
   - key masking: plain message, `%s` args, f-string, logged exception containing the key.

## Acceptance
- `.venv/bin/pytest -q` passes.
- `python -m crawler --help` lists the subcommands.
- `git status` shows old files deleted, new files untracked, nothing under `data/`/`logs/`/`.env` tracked.
- Smoke: create `.env` from `.env.example` with a real key and run `python -m crawler status` → prints loaded settings with the key masked.

## Hand-over
Reply with: files created/deleted, test summary, the smoke command, and anything you were unsure about. Then stop.
