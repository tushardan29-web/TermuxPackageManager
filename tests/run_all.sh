#!/bin/sh
# Run the full tpm test suite.
# Usage: ./tests/run_all.sh   (or: python3 -m unittest discover -s tests)
cd "$(dirname "$0")/.." || exit 1
exec python3 -m unittest discover -s tests -v
