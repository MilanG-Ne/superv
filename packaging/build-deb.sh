#!/bin/sh
# Build dist/superv_<version>_all.deb (needs only dpkg-deb).
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(cd "$ROOT" && python3 -c 'import superv; print(superv.__version__)')
PKG="$ROOT/build/superv_${VERSION}_all"

rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/usr/lib/superv" "$PKG/usr/bin" "$PKG/usr/share/applications" \
         "$PKG/usr/share/icons/hicolor/scalable/apps" "$PKG/etc/xdg/autostart" "$PKG/usr/share/doc/superv"

cp -R "$ROOT/superv" "$PKG/usr/lib/superv/"
find "$PKG" -name '__pycache__' -type d -prune -exec rm -rf {} +
install -m 755 "$ROOT/bin/superv" "$PKG/usr/bin/superv"
install -m 644 "$ROOT/data/superv.desktop" "$ROOT/data/superv-settings.desktop" "$PKG/usr/share/applications/"
install -m 644 "$ROOT/data/superv-autostart.desktop" "$PKG/etc/xdg/autostart/superv.desktop"
install -m 644 "$ROOT/superv/icons/superv.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/superv.svg"
install -m 644 "$ROOT/LICENSE" "$PKG/usr/share/doc/superv/copyright"
install -m 644 "$ROOT/README.md" "$PKG/usr/share/doc/superv/README.md"

# Standard permissions whatever the builder's umask is.
find "$PKG" -type d -exec chmod 755 {} +
find "$PKG/usr/lib" "$PKG/usr/share" "$PKG/etc" -type f -exec chmod 644 {} +

SIZE=$(du -sk "$PKG/usr" "$PKG/etc" | awk '{s += $1} END {print s}')
cat > "$PKG/DEBIAN/control" <<CONTROL
Package: superv
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), python3-gi, gir1.2-gtk-3.0, libx11-6, libxtst6
Recommends: xfconf
Installed-Size: $SIZE
Maintainer: MilanG-Ne <MilanG-Ne@users.noreply.github.com>
Homepage: https://github.com/MilanG-Ne/superv
Description: Windows-style clipboard history (Super+V) for Linux Lite and Xfce
 Press Super+V to see the last 25 things you copied, text and images, and
 paste any of them. Pin items to keep them after a reboot. Includes emoji,
 kaomoji and symbol pickers, paste as plain text, search, and a settings
 page modelled on the Windows one. Works on any X11 desktop.
CONTROL

echo "/etc/xdg/autostart/superv.desktop" > "$PKG/DEBIAN/conffiles"

cat > "$PKG/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    gtk-update-icon-cache -q -t /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
fi
POSTINST
chmod 755 "$PKG/DEBIAN/postinst"

mkdir -p "$ROOT/dist"
dpkg-deb --root-owner-group --build "$PKG" "$ROOT/dist/superv_${VERSION}_all.deb"
