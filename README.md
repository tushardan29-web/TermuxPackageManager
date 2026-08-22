# tpm — Termux Package Manager & Storage Analyzer

A fast, safe CLI/TUI tool for managing and analyzing installed packages on
Termux (Android), Debian/Ubuntu, and Arch Linux systems.

## Features

- **Multi-distro support**: works on Termux (Android), Debian/Ubuntu, and Arch Linux
- **Automatic backend detection**: `dpkg-query` → AptBackend; pacman local DB → PacmanBackend
- **Environment detection** in `src/tpm/environment.py` — discovers `$PREFIX`, OS, and active package manager dynamically
- **Package listing, search, info, dependencies, reverse dependencies**
- **Orphan detection** — find packages nothing depends on
- **Removal simulation** — preview what would be removed before acting
- **Pacman-specific removal** — `pacman -R` for Arch, `apt remove` for Debian
- **File listing** — `pacman -Ql` for files, `dpkg-query -L` for dpkg
- **Storage analysis** — detect caches including pacman package cache (`$PREFIX/var/cache/pacman/pkg`)
- **Interactive TUI** built on `rich`

## Installation

```sh
pip install -e .
```

No `sudo` required for installation. The tool runs as a normal user.

## Usage

```sh
# Launch interactive TUI
tpm

# List all packages
tpm list

# Search packages
tpm search <term>

# Package info
tpm info <package>

# Detect environment and package manager
tpm doctor

# Storage breakdown
tpm storage

# Simulate removal (no changes made)
tpm remove --simulate <package>
```

## Commands

| Command     | Description                                |
|-------------|--------------------------------------------|
| `scan`      | Scan installed packages into index          |
| `rescan`    | Force full rescan                           |
| `list`      | List packages (`--explicit`, `--orphans`, etc.) |
| `search`    | Search installed packages                   |
| `info`      | Package details                             |
| `deps`      | Direct/recursive dependencies               |
| `rdeps`     | Reverse dependencies (what requires this)   |
| `orphans`   | Packages nothing depends on                 |
| `files`     | Files owned by a package                    |
| `storage`   | Storage breakdown                           |
| `largest`   | Largest directories                         |
| `cache`     | Detect caches                               |
| `clean`     | Interactive cleanup review                  |
| `remove`    | Remove a package (simulate first)           |
| `doctor`    | Environment and health diagnostics          |
| `tui`       | Interactive TUI                             |

## Backend Detection

`tpm doctor` shows the detected environment and package manager. The backend
is selected by `detect_backend()` in `src/tpm/package/backend.py`:

1. Checks `TERMUX_APP_PACKAGE_MANAGER` env var (apt or pacman)
2. Falls back to checking for dpkg DB presence (`$PREFIX/var/lib/dpkg/status`)
3. Checks for pacman local DB (`$PREFIX/var/lib/pacman/local/`)
4. Prefers the active manager when both databases are present

## How It Works

1. **Scan**: Reads the real package database (dpkg status or pacman desc files)
2. **Index**: Stores package metadata + dependency graph in a local SQLite database
3. **Analyze**: Classifies packages as essential, explicit, shared, or orphan
4. **Act**: Simulate or execute removals with full safety checks

## Testing

```sh
python3 -m unittest discover -s tests
```

6 test files covering parser, graph, simulator, formatter, validation, and database.
