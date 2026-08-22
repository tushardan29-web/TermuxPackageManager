# Architecture

## Overview

tpm is a presentation-layer CLI/TUI over a shared core library. The CLI
(`src/tpm/cli.py`) and TUI (`src/tpm/tui.py`) both use the same backend
modules — no logic is duplicated.

## Modules

| Module                          | Purpose                                      |
|---------------------------------|----------------------------------------------|
| `cli.py`                        | Argparse entry point, command dispatch        |
| `tui.py`                        | Interactive TUI (rich-based)                  |
| `core.py`                       | State loading, freshness checks               |
| `scanner.py`                    | Scan installed packages into SQLite index      |
| `database.py`                   | SQLite storage for package metadata            |
| `formatter.py`                  | Size formatting                               |
| `validation.py`                 | Package name validation                       |
| `environment.py`                | Environment detection (Termux vs Linux)        |
| `dependency/parser.py`          | Dependency expression parsing                 |
| `dependency/graph.py`           | Dependency graph (direct/reverse deps)         |
| `removal/simulator.py`          | Simulate package removal                      |
| `package/backend.py`            | Backend abstraction + AptBackend + PacmanBackend |
| `storage/scanner.py`            | Filesystem/cache scanner                      |

## Backend Abstraction

`src/tpm/package/backend.py` defines the `PackageManagerBackend` interface
and two concrete implementations:

### AptBackend (dpkg/apt)
- Uses `dpkg-query -W -f=...` with STX-separated records (multiline-safe;
  NUL cannot appear in subprocess arguments)
- Uses `apt-mark showmanual` for explicitly installed packages
- Uses `dpkg-query -L` for file lists
- Status file: `$PREFIX/var/lib/dpkg/status`

### PacmanBackend
- Reads `desc` files directly from `$PREFIX/var/lib/pacman/local/*/desc`
  — no subprocess for metadata, just file parsing
- Uses `pacman -Ql` for file lists
- `%REASON%` field: 0 = explicitly installed, 1 = dependency

### Backend Selection: `detect_backend()`

1. Runs `detect_environment()` to get active manager preference
2. Checks if dpkg DB exists and dpkg-query is available → AptBackend
3. Checks if pacman local DB has content → PacmanBackend
4. When both backends are available, prefers the one matching the active manager
5. Returns `None` if no backend is available

Both backends produce identical record dicts for the scanner, so the rest
of tpm is backend-agnostic.

## Environment Detection

`src/tpm/environment.py` provides `detect_environment()` which returns:

```python
{
    "termux": bool,       # True if on Termux (Android)
    "prefix": str,        # $PREFIX or /usr
    "platform": str,      # platform.system()
    "machine": str,       # platform.machine()
    "os_name": str|None,  # from /etc/os-release NAME=
    "manager": str|None,  # "apt" | "pacman" | None
}
```

Detection order:
1. `TERMUX_APP_PACKAGE_MANAGER` env var (Termux exposes this explicitly)
2. dpkg DB presence + dpkg-query binary → `"apt"`
3. Pacman local DB presence → `"pacman"`
4. dpkg-query binary alone → `"apt"` (fallback)

## Data Flow

```
scan → backend.list_installed() → SQLite index
                                      ↓
cli/tui → core.load_state() → State(graph, explicit, essential)
                                      ↓
                           classification → ESSENTIAL/EXPLICIT/SHARED/ORPHAN/SINGLE-USE
                                      ↓
                           removal.simulate_removal() → Simulation result
                                      ↓
                           backend removal → apt remove OR pacman -R
```
