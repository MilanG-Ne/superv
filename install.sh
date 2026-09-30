#!/bin/sh
# Install SuperV.
#
#   ./install.sh            for your user only, no root needed (~/.local)
#   sudo ./install.sh --system   for every user on this computer (/usr/local)
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
MODE=user
if [ "${1:-}" = "--system" ]; then
    MODE=system
elif [ -n "${1:-}" ]; then
    echo "usage: $0 [--system]" >&2
    exit 2
fi

if ! python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' 2>/dev/null; then
    echo "SuperV needs Python 3 with GTK 3 bindings. Install them with:" >&2
    echo "  sudo apt install python3-gi gir1.2-gtk-3.0" >&2
    exit 1
fi

if [ "$MODE" = system ]; then
    if [ "$(id -u)" -ne 0 ]; then
        echo "--system needs root: sudo $0 --system" >&2
        exit 1
    fi
    PREFIX=/usr/local
    AUTOSTART_DIR=/etc/xdg/autostart
else
    PREFIX="$HOME/.local"
    AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
fi

LIB="$PREFIX/lib/superv"
BIN="$PREFIX/bin/superv"
APPS="$PREFIX/share/applications"
ICONS="$PREFIX/share/icons/hicolor"

echo "Installing SuperV to $PREFIX"
rm -rf "$LIB/superv"
mkdir -p "$LIB" "$APPS" "$ICONS/scalable/apps" "$AUTOSTART_DIR" "$(dirname "$BIN")"
cp -R "$HERE/superv" "$LIB/"
find "$LIB" -name '__pycache__' -type d -prune -exec rm -rf {} +
install -m 755 "$HERE/bin/superv" "$BIN"
install -m 644 "$HERE/superv/icons/superv.svg" "$ICONS/scalable/apps/superv.svg"
# Absolute paths: ~/.local/bin may not be on PATH until the next login.
for name in superv.desktop superv-settings.desktop; do
    sed "s|^Exec=superv|Exec=$BIN|" "$HERE/data/$name" > "$APPS/$name"
done
sed "s|^Exec=superv|Exec=$BIN|" "$HERE/data/superv-autostart.desktop" > "$AUTOSTART_DIR/superv.desktop"
gtk-update-icon-cache -q -t "$ICONS" 2>/dev/null || true
update-desktop-database -q "$APPS" 2>/dev/null || true

if [ "$MODE" = system ]; then
    echo
    echo "Done. Each user gets SuperV at their next login, or right away by"
    echo "opening \"SuperV Clipboard\" from the menu. Super+V is set up on first start (Xfce)."
    exit 0
fi

"$BIN" quit >/dev/null 2>&1 || true   # restart an older running copy
sleep 0.5
"$BIN" setup-shortcuts || true
"$BIN" start
echo
echo "Done. Copy a few things, then press Super+V."
echo "Settings: open \"SuperV Settings\" from the menu, or run: $BIN settings"
