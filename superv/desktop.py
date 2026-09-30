"""Desktop integration: the Super+V / Super+. shortcuts and starting at login.

Shortcuts are registered with Xfce's own keyboard-shortcut settings (the
same place as Settings > Keyboard > Application Shortcuts), so there's no
key grabbing in SuperV itself. On other desktops the user binds
`superv toggle` by hand; the README explains how.
"""

import os
import shlex
import shutil
import subprocess

from . import APP_ID, paths

CHANNEL = "xfce4-keyboard-shortcuts"
# Xfce shortcut -> superv arguments. Super+. opens the emoji panel, like Win+.
SHORTCUTS = {
    "<Super>v": "toggle",
    "<Super>period": "toggle --tab emoji",
}
AUTOSTART_NAME = APP_ID + ".desktop"
SYSTEM_AUTOSTART = os.path.join("/etc/xdg/autostart", AUTOSTART_NAME)


def launcher():
    """Absolute path of the `superv` command, for shortcuts and autostart."""
    path = os.environ.get("SUPERV_LAUNCHER")
    if path and os.path.isfile(path):
        return path
    return shutil.which(APP_ID) or APP_ID


def command(args):
    return "%s %s" % (shlex.quote(launcher()), args)


# ---- shortcuts -------------------------------------------------------------

def is_xfce():
    return "XFCE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper() and shutil.which("xfconf-query") is not None


def _xfconf(*args):
    try:
        result = subprocess.run(["xfconf-query", "-c", CHANNEL] + list(args),
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _is_ours(value):
    try:
        words = shlex.split(value or "")
    except ValueError:
        return False
    return bool(words) and os.path.basename(words[0]) == APP_ID


def shortcut_status():
    """{shortcut: (current command or None, is it ours)}"""
    status = {}
    for key in SHORTCUTS:
        value = _xfconf("-p", "/commands/custom/" + key)
        status[key] = (value, _is_ours(value or ""))
    return status


def setup_shortcuts():
    """Register the shortcuts in Xfce. Never overwrites a shortcut the user
    already uses for something else. Returns (registered, conflicts)."""
    registered, conflicts = [], []
    if not is_xfce():
        return registered, conflicts
    for key, args in SHORTCUTS.items():
        current = _xfconf("-p", "/commands/custom/" + key)
        if current and not _is_ours(current):
            conflicts.append((key, current))
            continue
        if _xfconf("-n", "-t", "string", "-p", "/commands/custom/" + key, "-s", command(args)) is not None:
            registered.append(key)
    return registered, conflicts


def remove_shortcuts():
    removed = []
    if not shutil.which("xfconf-query"):
        return removed
    for key in SHORTCUTS:
        current = _xfconf("-p", "/commands/custom/" + key)
        if current and _is_ours(current) and _xfconf("-r", "-p", "/commands/custom/" + key) is not None:
            removed.append(key)
    return removed


def pretty_shortcut(key):
    return key.replace("<Super>", "Super+").replace("period", ".").replace("+v", "+V")


# ---- autostart -------------------------------------------------------------

def _user_autostart():
    return os.path.join(paths.autostart_dir(), AUTOSTART_NAME)


def _read_hidden(path):
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                key, _, value = line.partition("=")
                if key.strip() in ("Hidden", "X-GNOME-Autostart-enabled"):
                    hidden = value.strip().lower() == "true"
                    if key.strip() == "X-GNOME-Autostart-enabled":
                        hidden = not hidden
                    if hidden:
                        return True
    except OSError:
        return None
    return False


def autostart_enabled():
    hidden = _read_hidden(_user_autostart())
    if hidden is not None:
        return not hidden
    return os.path.exists(SYSTEM_AUTOSTART)


def set_autostart(enabled):
    """Write ~/.config/autostart/superv.desktop. A user file with Hidden=true
    also switches off the system-wide one that the .deb installs."""
    paths.ensure_dir(paths.autostart_dir())
    entry = "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=SuperV clipboard history",
        "Comment=Clipboard history for Super+V",
        "Exec=%s" % command("daemon"),
        "Icon=superv",
        "Terminal=false",
        "NoDisplay=true",
        "X-GNOME-Autostart-enabled=%s" % ("true" if enabled else "false"),
        "Hidden=%s" % ("false" if enabled else "true"),
        "",
    ])
    paths.write_atomic(_user_autostart(), entry.encode("utf-8"))


def manual_shortcut_hint():
    return "Bind the command %s to Super+V in your desktop's keyboard settings." % command("toggle")
