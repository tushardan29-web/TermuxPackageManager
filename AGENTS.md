# AGENTS.md

## Project

tpm — Termux Package Manager & Storage Analyzer. Works on Termux (Android),
Debian/Ubuntu, and Arch Linux.

## Key Modules

- `src/tpm/environment.py` — `detect_environment()`: discovers `$PREFIX`, OS,
  and active package manager (apt/pacman)
- `src/tpm/package/backend.py` — `PackageManagerBackend` base class with
  `AptBackend` (dpkg) and `PacmanBackend` (pacman). `detect_backend()` picks
  the right one based on `detect_environment()`.
- `src/tpm/cli.py` — CLI commands (scan, list, search, info, deps, rdeps,
  orphans, files, storage, largest, cache, clean, remove, doctor, tui)
- `src/tpm/tui.py` — Interactive TUI (rich-based, same core library as CLI)
- `src/tpm/scanner.py` — Scans backend into SQLite index
- `src/tpm/storage/scanner.py` — Filesystem scanner, detects pacman package cache

## Backend Details

Both backends produce identical record dicts:
- `name`, `version`, `architecture`, `status`, `installed_size`,
  `explicitly_installed`, `essential`, `priority`, `source`, `description`,
  `depends_groups`

Backend-specific details:
- **AptBackend**: reads `/var/lib/dpkg/status` via `dpkg-query -W -f=...`
  with STX separation; `apt-mark showmanual` for explicit list
- **PacmanBackend**: reads `$PREFIX/var/lib/pacman/local/*/desc` files
  directly (no subprocess for metadata); `pacman -Ql` for file lists;
  `%REASON%` field determines explicit/dependency status

## Testing

```sh
python3 -m unittest discover -s tests
```

6 test files: test_parser, test_graph, test_simulator, test_formatter,
test_validation, test_database.
