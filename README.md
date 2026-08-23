# tpm - Termux Package Manager & Storage Analyzer

A safe, intelligent CLI/TUI tool that sits on top of your existing package
manager to answer: *What is consuming my storage? What depends on what?
What happens if I remove package X?*

**tpm does NOT replace apt/dpkg/pacman.** It analyzes, classifies, and
simulates -- then delegates actual operations to the real package manager.

## Quick Start

```sh
# Install
cd tpm && bash install.sh

# Or directly
pip install .

# Or from .deb (Termux)
dpkg -i dist/tpm_0.1.0_aarch64.deb
```

```sh
# Index your installed packages
tpm scan

# See what is taking space
tpm list --largest

# Check a specific package
tpm info python

# Preview removal safely (no system changes)
tpm remove --simulate python

# Find orphans
tpm orphans

# Interactive TUI
tpm tui
```

## Features

| Category | Capabilities |
|----------|-------------|
| Package analysis | List, search, info, dependencies, reverse dependencies |
| Dependency classification | EXPLICIT, SHARED, SINGLE-USE, ORPHAN, ESSENTIAL |
| Removal simulation | Preview cascade effects before any system change |
| Storage analysis | Home, $PREFIX, caches, proot-distro, per-category breakdown |
| Cache cleanup | APT, pip, npm, cargo, gradle, pacman, uv -- with interactive confirmation |
| File listing | Per-package files, largest files, lazy-populated on demand |
| Health checks | Environment detection, dependency consistency, broken packages |
| Dual backend | apt/dpkg (Debian/Termux) + pacman (Arch) + uv (Python) with auto-detection |
| JSON output | Every command supports --json for scripting |
| Interactive TUI | curses-based terminal UI with auto-resize, scroll indicators, mouse+keyboard |
| Delete operations | Storage file/folder deletion, package uninstall with simulation and confirmation |

## Installation Requirements

- Python 3.10 or later
- dpkg-query, pacman, or uv (for package data)
- No root required

## Supported Platforms

- **Termux** (Android) -- primary target
- **Debian/Ubuntu** -- fully supported
- **Arch Linux** -- fully supported via pacman backend

## Safety Model

- Never deletes package files directly
- Delegates to apt remove / pacman -R after explicit confirmation
- Simulate before every destructive operation
- Package names validated before any subprocess call
- SQLite database is a cache, never authoritative
- Running as root triggers a warning

## Documentation

- [User Guide](GUIDE.md) -- complete command reference with examples
- [Architecture](ARCHITECTURE.md) -- internal design and data flow
- [Security Model](SECURITY.md) -- safety guarantees and subprocess controls
- [Contributing](CONTRIBUTING.md) -- development workflow and test instructions

## Testing

```sh
python3 -m unittest discover -s tests
```

63 unit tests covering: dependency parser, graph, removal simulator,
formatter, validation, database, pacman backend, cleanup, config.

## License

MIT
