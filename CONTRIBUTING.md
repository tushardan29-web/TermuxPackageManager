# Contributing

## Development Setup

```sh
pip install -e .
```

## Running Tests

```sh
python3 -m unittest discover -s tests
```

6 test files covering: parser, graph, simulator, formatter, validation, database.

You can also use the helper script:

```sh
./tests/run_all.sh
```

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the module layout and data flow.

## Code Style

- Python 3.10+ required
- No comments unless requested
- Keep presentation (CLI/TUI) separate from core logic
- Backend implementations must produce identical record dicts
- Validate package names before any subprocess call

## Multi-distro Notes

When adding features, consider both backends:
- AptBackend uses dpkg-query (STX-separated records)
- PacmanBackend reads desc files directly (no subprocess for metadata)
- Use `detect_backend()` to get the active backend
- The rest of the codebase should be backend-agnostic
