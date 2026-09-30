#!/bin/sh
# Remove SuperV installed with install.sh.
#
#   ./uninstall.sh                remove the per-user install
#   ./uninstall.sh --purge        ...and delete your history, pins and settings
#   sudo ./uninstall.sh --system  remove the system-wide install
set -eu

MODE=user
PURGE=no
for arg in "$@"; do
    case "$arg" in
        --system) MODE=system ;;
        --purge) PURGE=yes ;;
        *) echo "usage: $0 [--system] [--purge]" >&2; exit 2 ;;
    esac
done

if [ "$MODE" = system ]; then
    PREFIX=/usr/local
    AUTOSTART="/etc/xdg/autostart/superv.desktop"
else
    PREFIX="$HOME/.local"
    AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/superv.desktop"
fi
BIN="$PREFIX/bin/superv"

if [ "$MODE" = user ] && [ -x "$BIN" ]; then
    "$BIN" quit >/dev/null 2>&1 || true
    "$BIN" remove-shortcuts || true
fi

rm -rf "$PREFIX/lib/superv"
rm -f "$BIN" "$PREFIX/share/applications/superv.desktop" "$PREFIX/share/applications/superv-settings.desktop" \
      "$PREFIX/share/icons/hicolor/scalable/apps/superv.svg" "$AUTOSTART"

if [ "$PURGE" = yes ]; then
    rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/superv" "${XDG_DATA_HOME:-$HOME/.local/share}/superv"
    echo "Removed SuperV and your clipboard history, pins and settings."
else
    echo "Removed SuperV. Your pins and settings are still in ~/.config/superv and ~/.local/share/superv"
    echo "(run with --purge to delete them)."
fi
if [ "$MODE" = system ]; then
    echo "Users who set up Super+V should remove it in Settings > Keyboard > Application Shortcuts."
fi
