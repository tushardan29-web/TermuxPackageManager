#!/bin/sh
# tpm installer
# Installs tpm in development mode. No sudo required.
set -e

echo "Installing tpm..."
pip install -e .
echo "Done. Run 'tpm' to launch."
