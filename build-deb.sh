#!/bin/bash
# build-deb.sh — Build a .deb package for tpm on Termux
set -e

VERSION="0.1.0"
ARCH="aarch64"
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
PKG_NAME="tpm"

rm -rf build/deb
STAGING="build/deb/${PKG_NAME}_${VERSION}_${ARCH}"
mkdir -p "$STAGING/DEBIAN"
mkdir -p "$STAGING/$PREFIX/bin"
mkdir -p "$STAGING/$PREFIX/share/doc/$PKG_NAME"

echo "Building tpm $VERSION .deb for $ARCH..."

# Install Python source into site-packages
SITE=$(python3 -c "import site; print(site.getsitepackages()[0])")
DEST_SITE="$STAGING/$SITE/tpm"
mkdir -p "$DEST_SITE"
cp -r src/tpm/* "$DEST_SITE/"
cp src/tpm/__init__.py "$DEST_SITE/"

# Wrapper script
cat > "$STAGING/$PREFIX/bin/$PKG_NAME" << 'WRAPPER'
#!/data/data/com.termux/files/usr/bin/bash
exec python3 -m tpm.cli "$@"
WRAPPER
chmod 755 "$STAGING/$PREFIX/bin/$PKG_NAME"

# Docs
for f in README.md LICENSE ARCHITECTURE.md SECURITY.md; do
    [ -f "$f" ] && cp "$f" "$STAGING/$PREFIX/share/doc/$PKG_NAME/"
done

# Control file
INSTALLED_SIZE=$(du -sk "$STAGING" | cut -f1)
cat > "$STAGING/DEBIAN/control" << EOF
Package: $PKG_NAME
Version: $VERSION
Architecture: $ARCH
Depends: python (>= 3.10), python-rich
Maintainer: tpm contributors
Installed-Size: $INSTALLED_SIZE
Priority: optional
Section: utils
Homepage: https://github.com/tpm/tpm
Description: Termux Package Manager & Storage Analyzer
 Intelligent package/dependency/storage analysis for Termux.
 Features: dependency graph, removal simulation, storage analyzer,
 cache cleanup, interactive TUI. Dual backend: apt/dpkg + pacman.
 Does NOT replace apt/dpkg — sits on top of existing infrastructure.
EOF

# Fix permissions (dpkg-deb requires DEBIAN >= 0755)
find "$STAGING" -type d -exec chmod 755 {} \;
find "$STAGING" -type f -exec chmod 644 {} \;
chmod 755 "$STAGING/$PREFIX/bin/$PKG_NAME"

dpkg-deb --build "$STAGING" "dist/${PKG_NAME}_${VERSION}_${ARCH}.deb"
echo ""
echo "Built: dist/${PKG_NAME}_${VERSION}_${ARCH}.deb"
echo "Install: dpkg -i dist/${PKG_NAME}_${VERSION}_${ARCH}.deb"
