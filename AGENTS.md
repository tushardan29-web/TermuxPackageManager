# AGENTS.md

## Project

tpm v0.1.0 -- Termux Package Manager & Storage Analyzer.
Works on Termux (Android), Debian/Ubuntu, and Arch Linux.
Python 3.10+, rich for TUI, SQLite for index.

## Key Modules

| Module | Purpose |
|--------|---------|
| `src/tpm/cli.py` | 18 CLI commands, argparse, JSON output |
| `src/tpm/tui.py` | Rich-based interactive TUI, 10 screen types |
| `src/tpm/core.py` | SystemState loading, freshness checks |
| `src/tpm/scanner.py` | Scan orchestration, staleness detection |
| `src/tpm/database.py` | SQLite index, schema, read/write |
| `src/tpm/config.py` | TOML-like config reader |
| `src/tpm/cleanup.py` | Cache cleanup backends |
| `src/tpm/environment.py` | Termux vs Linux detection |
| `src/tpm/formatter.py` | Binary size formatting |
| `src/tpm/validation.py` | Package name validation |
| `src/tpm/dependency/parser.py` | Debian dependency expressions |
| `src/tpm/dependency/graph.py` | Adjacency graph, classification |
| `src/tpm/removal/simulator.py` | Graph reachability simulation |
| `src/tpm/package/backend.py` | AptBackend + PacmanBackend |
| `src/tpm/storage/scanner.py` | Filesystem scanner, cache detection |

## Backend Details

Both backends produce identical record dicts:
- `name`, `version`, `architecture`, `status`, `installed_size`,
  `explicitly_installed`, `essential`, `priority`, `source`, `description`,
  `depends_groups`

**AptBackend**: reads dpkg status via `dpkg-query -W -f=...` with STX
separator; `apt-mark showmanual` for explicit list.

**PacmanBackend**: reads `$PREFIX/var/lib/pacman/local/*/desc` files
directly; `pacman -Ql` for file lists; `%REASON%` for explicit/dependency.

## Testing

```sh
python3 -m unittest discover -s tests
```

8 test files: test_parser, test_graph, test_simulator, test_formatter,
test_validation, test_database, test_pacman, test_cleanup.

## Safety Rules

- Never run `apt remove` or `pacman -R` in tests/development
- Never delete package files directly
- Read-only inspection only during development
- Subprocess argument arrays, never shell strings
- Validate package names via `tpm.validation`

## CLI Commands

scan, rescan, list, search, info, deps, rdeps, orphans, files,
storage, largest, cache, clean, remove, doctor, tui, version, help

## JSON Output

Every command supports `--json`. Stdout is valid JSON only; errors
go to stderr.

## Installation

```sh
pip install -e .       # development
pip install .          # install
dpkg -i dist/*.deb     # Termux .deb
bash install.sh        # guided install
```
