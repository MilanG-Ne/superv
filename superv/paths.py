"""Where SuperV keeps its files. Everything follows the XDG base directory spec.

This module must stay free of GTK imports: the tiny `superv toggle` client that
runs on every Super+V press imports it, and it has to start fast.
"""

import os
import re

from . import APP_ID


def _base(var, fallback):
    value = os.environ.get(var, "")
    return value if os.path.isabs(value) else os.path.expanduser(fallback)


def config_dir():
    return os.path.join(_base("XDG_CONFIG_HOME", "~/.config"), APP_ID)


def data_dir():
    """Pinned items (and the full history, if the user keeps it) live here."""
    return os.path.join(_base("XDG_DATA_HOME", "~/.local/share"), APP_ID)


def runtime_dir():
    """Unpinned history lives here. XDG_RUNTIME_DIR is emptied on logout and
    reboot, which gives us the Windows behaviour for free."""
    base = os.environ.get("XDG_RUNTIME_DIR", "")
    if os.path.isabs(base) and os.path.isdir(base):
        return os.path.join(base, APP_ID)
    return os.path.join(_base("XDG_CACHE_HOME", "~/.cache"), APP_ID, "runtime")


def autostart_dir():
    return os.path.join(_base("XDG_CONFIG_HOME", "~/.config"), "autostart")


def display_id():
    """One daemon per X display. ':0' and ':0.0' must map to the same id."""
    match = re.match(r"^[^:]*:(\d+)", os.environ.get("DISPLAY", ""))
    return match.group(1) if match else "none"


def socket_path():
    return os.path.join(runtime_dir(), "superv-%s.sock" % display_id())


def ensure_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def write_atomic(path, data, sync=False):
    """Write bytes to `path` so readers never see a half-written file."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        if sync:
            handle.flush()
            os.fsync(handle.fileno())
    os.replace(tmp, path)
