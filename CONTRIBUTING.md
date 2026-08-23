# Contributing

## Development Setup

```sh
git clone <repo-url> tpm
cd tpm
pip install -e .
```

No virtualenv needed. tpm runs directly in the system Python.

## Running Tests

```sh
python3 -m unittest discover -s tests
```

Or:

```sh
bash tests/run_all.sh
```

63 tests covering all core modules.

## Test Structure

| Test file | Coverage |
|-----------|----------|
| `test_parser.py` | Dependency expression parsing (7 tests) |
| `test_graph.py` | Graph construction, classification, cycle safety (9 tests) |
| `test_simulator.py` | Removal simulation cascade, protection, broken detection (12 tests) |
| `test_formatter.py` | Size formatting, binary units (6 tests) |
| `test_validation.py` | Package name validation (7 tests) |
| `test_database.py` | SQLite roundtrip, alternative groups (3 tests) |
| `test_pacman.py` | PacmanBackend with mock desc files (8 tests) |
| `test_cleanup.py` | Cleanup backends, config loading (9 tests) |

## Architecture Rules

1. **Core library is shared.** CLI and TUI use the same modules.
   Never duplicate business logic.

2. **No shell strings.** Use `subprocess.run(["cmd", arg1, arg2])`,
   never `os.system("cmd " + arg)`.

3. **Validate first.** Call `validate_package_name()` before any
   subprocess that takes a package name.

4. **SQLite is a cache.** Never trust it over the real system.
   Always check staleness before destructive operations.

5. **One formatter.** Use `format_size()` from `formatter.py` for all
   size display. Do not duplicate formatting logic.

6. **No root required.** tpm must work as a normal user.

## Code Style

- Python 3.10+ (f-strings, type hints, `from __future__ import annotations`)
- No external dependencies beyond `rich`
- Functions should be short and focused
- Docstrings on all public functions
- No comments explaining "what" -- use clear naming instead

## Adding a New Command

1. Add the command function in `cli.py`: `def cmd_foo(args, ui, db)`
2. Add the subparser in `build_parser()`
3. Add JSON output support (`args.json` check)
4. Add tests for the core logic
5. Update `GUIDE.md` with documentation

## Adding a New Backend

1. Subclass `PackageManagerBackend` in `package/backend.py`
2. Implement `available()`, `list_installed()`, `explicit_names()`,
   `file_list()`
3. Add detection logic in `detect_backend()`
4. Add tests with mock data
5. Update `ARCHITECTURE.md`

## Commit Style

Use conventional commits:

```
feat: add new feature
fix: fix a bug
docs: update documentation
test: add tests
refactor: restructure code
```
