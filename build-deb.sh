#!/bin/bash
# build-deb.sh -- Build a .deb package for tpm.
#
# Defaults to the Termux $PREFIX (/data/data/com.termux/files/usr) when run
# on Termux, and /usr when run on Debian/Ubuntu. Override with --prefix=PATH.
set -euo pipefail

VERSION="0.1.0"
PKG_NAME="tpm"

detect_prefix() {
    if [ -n "${TERMUX_VERSION:-}" ] || [ -d "/data/data/com.termux/files/usr" ]; then
        echo "/data/data/com.termux/files/usr"
    else
        echo "/usr"
    fi
}

PREFIX=""
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix=*)   PREFIX="${1#*=}" ;;
        --prefix)     PREFIX="$2"; shift ;;
        --version=*)  VERSION="${1#*=}" ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
    shift
done

if [ -z "$PREFIX" ]; then
    PREFIX="$(detect_prefix)"
fi

# Architecture: trust dpkg if available, else 'all'
if command -v dpkg >/dev/null 2>&1; then
    ARCH="$(dpkg --print-architecture 2>/dev/null || echo all)"
else
    ARCH="all"
fi

OUT_DEB="dist/${PKG_NAME}_${VERSION}_${ARCH}.deb"
STAGING="build/deb/${PKG_NAME}_${VERSION}_${ARCH}"

rm -rf "$STAGING"
mkdir -p "$STAGING/DEBIAN"
mkdir -p "$STAGING/${PREFIX}/bin"
mkdir -p "$STAGING/${PREFIX}/lib/tpm"
mkdir -p "$STAGING/${PREFIX}/share/doc/${PKG_NAME}"

echo "Building ${PKG_NAME} ${VERSION} for arch=${ARCH}, PREFIX=${PREFIX}..."

# Copy Python source into staged lib/tpm. Strip __pycache__ because .pyc files
# are version-specific (cpython-314.pyc) and useless on other Python versions.
# Use a fixed $PREFIX/lib/tpm layout rather than host site-packages so the
# .deb never contains host-built absolute paths (e.g. /opt/hostedtoolcache
# when built on GitHub Actions).
if [ ! -d "src/tpm" ]; then
    echo "error: src/tpm not found; build from the project root" >&2
    exit 1
fi
(cd src/tpm && tar --exclude='__pycache__' --exclude='*.pyc' -cf - .) \
    | tar -xf - -C "$STAGING/${PREFIX}/lib/tpm"

# Wrapper: /usr/bin/env python3 (works on Termux and Debian), and an explicit
# sys.path so the package is found regardless of which site-packages dirs the
# runtime Python knows about.
cat > "$STAGING/${PREFIX}/bin/${PKG_NAME}" << 'WRAPPER'
#!/usr/bin/env python3
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib", "tpm"))
from tpm.cli import main
if __name__ == "__main__":
    main()
WRAPPER
chmod 755 "$STAGING/${PREFIX}/bin/${PKG_NAME}"

# Docs
for f in README.md LICENSE ARCHITECTURE.md SECURITY.md; do
    [ -f "$f" ] && cp "$f" "$STAGING/${PREFIX}/share/doc/${PKG_NAME}/"
done

# Control file. 'Architecture: all' because the wrapper is pure Python and
# works on Termux (aarch64/arm/i686) and Debian/Ubuntu without rebuild.
INSTALLED_SIZE=$(du -sk "$STAGING" | cut -f1)
cat > "$STAGING/DEBIAN/control" << EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Architecture: all
Depends: python3
Maintainer: tpm contributors
Installed-Size: ${INSTALLED_SIZE}
Priority: optional
Section: utils
Homepage: https://github.com/tushardan29-web/TermuxPackageManager
Description: Termux Package Manager & Storage Analyzer
 Intelligent package/dependency/storage analysis for Termux and Linux.
 Features: dependency graph, removal simulation, storage analyzer,
 cache cleanup, interactive TUI. Dual backend: apt/dpkg + pacman + uv.
 Does NOT replace apt/dpkg -- sits on top of existing infrastructure.
EOF

# dpkg-deb requires DEBIAN >= 0755 and files need sane perms
find "$STAGING" -type d -exec chmod 755 {} \;
find "$STAGING" -type f -exec chmod 644 {} \;
chmod 755 "$STAGING/${PREFIX}/bin/${PKG_NAME}"

mkdir -p dist
dpkg-deb --build "$STAGING" "$OUT_DEB"

echo ""
echo "Built: $OUT_DEB"
echo "Inspect: dpkg-deb -c $OUT_DEB"
echo "Install: dpkg -i $OUT_DEB"