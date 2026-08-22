#!/bin/bash
# tpm installer — Termux Package Manager & Storage Analyzer
# Works on Termux (Android), Debian/Ubuntu, Arch Linux.
# No root required.
set -e

echo "tpm installer"
echo "============="
echo ""

# ── preflight checks ────────────────────────────────────────────────────
check_cmd() { command -v "$1" >/dev/null 2>&1; }

if ! check_cmd python3; then
    echo "error: python3 is required but not found"
    exit 1
fi

PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  python3: $PYTHON_VER"

if ! check_cmd pip3 && ! check_cmd pip; then
    echo "error: pip is required but not found (install python-pip or python3-pip)"
    exit 1
fi

PIP="pip3"
check_cmd pip3 || PIP="pip"
echo "  pip:     $PIP"

# ── XDG directories ─────────────────────────────────────────────────────
XDG_DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
XDG_STATE="${XDG_STATE_HOME:-$HOME/.local/state}"
XDG_CONFIG="${XDG_CONFIG_HOME:-$HOME/.local/config}"

mkdir -p "$XDG_DATA/tpm" "$XDG_STATE/tpm" "$XDG_CONFIG/tpm"

echo "  data:    $XDG_DATA/tpm/"
echo "  state:   $XDG_STATE/tpm/"
echo "  config:  $XDG_CONFIG/tpm/"

# ── install ─────────────────────────────────────────────────────────────
echo ""
echo "Installing tpm..."

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
    # Install from local source (development or cloned repo)
    $PIP install --user -e "$SCRIPT_DIR" 2>&1 | grep -E "^(Successfully|ERROR)" || true
else
    # Install from built wheel if available
    WHL=$(find "$SCRIPT_DIR" -name "tpm-*.whl" -type f | head -1)
    if [ -n "$WHL" ]; then
        $PIP install --user "$WHL" 2>&1 | grep -E "^(Successfully|ERROR)" || true
    else
        # Install from PyPI (future)
        $PIP install --user tpm 2>&1 | grep -E "^(Successfully|ERROR)" || true
    fi
fi

# ── verify ──────────────────────────────────────────────────────────────
if command -v tpm >/dev/null 2>&1; then
    echo ""
    echo "✓ tpm installed successfully"
    echo "  $(tpm version)"
    echo ""
    echo "Quick start:"
    echo "  tpm scan         # index installed packages"
    echo "  tpm list         # list all packages"
    echo "  tpm info python  # package details"
    echo "  tpm remove --simulate python  # see what would happen"
    echo "  tpm tui          # interactive terminal UI"
    echo ""
else
    echo ""
    echo "⚠ tpm installed but not on PATH"
    echo "  Add to your shell profile:"
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
fi
