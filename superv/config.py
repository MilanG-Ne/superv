"""User settings, stored as JSON in ~/.config/superv/config.json."""

import json
import os
import sys

from . import paths

DEFAULTS = {
    # Clipboard history
    "history_enabled": True,
    "max_items": 25,  # like Windows; pinned items don't count
    "max_item_mb": 8,  # Windows uses 4; a bit more so large screenshots fit
    "capture_images": True,
    "keep_history_after_restart": False,  # Windows keeps only pinned items
    "pinned_first": False,
    # Pasting
    "auto_paste": True,
    "restore_on_owner_exit": True,
    # Privacy
    "ignore_secrets": True,
    # Look and startup
    "popup_position": "pointer",  # "pointer" or "center"
    "show_tray_icon": True,
    "shortcuts_offered": False,
    # Remembered picks for the emoji / kaomoji / symbol tabs
    "recent_emoji": [],
    "recent_kaomoji": [],
    "recent_symbols": [],
}

LIMITS = {
    "max_items": (1, 1000),
    "max_item_mb": (1, 200),
}

CHOICES = {
    "popup_position": ("pointer", "center"),
}


def _valid(key, value):
    default = DEFAULTS[key]
    if type(value) is not type(default):
        return False
    if key in LIMITS:
        low, high = LIMITS[key]
        return low <= value <= high
    if key in CHOICES:
        return value in CHOICES[key]
    if isinstance(default, list):
        return all(isinstance(entry, str) for entry in value)
    return True


class Config:
    def __init__(self, path=None):
        self.path = path or os.path.join(paths.config_dir(), "config.json")
        self._values = {key: (list(v) if isinstance(v, list) else v) for key, v in DEFAULTS.items()}
        self._listeners = []
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as error:
            print("superv: ignoring unreadable config %s: %s" % (self.path, error), file=sys.stderr)
            return
        if not isinstance(data, dict):
            return
        for key in DEFAULTS:
            if key in data and _valid(key, data[key]):
                self._values[key] = data[key]

    def save(self):
        paths.ensure_dir(os.path.dirname(self.path))
        text = json.dumps(self._values, indent=2, ensure_ascii=False, sort_keys=True)
        paths.write_atomic(self.path, (text + "\n").encode("utf-8"))

    def __getitem__(self, key):
        return self._values[key]

    def set(self, key, value):
        if not _valid(key, value):
            raise ValueError("invalid value for %s: %r" % (key, value))
        if self._values[key] == value:
            return
        self._values[key] = value
        self.save()
        for callback in list(self._listeners):
            callback(key, value)

    def connect(self, callback):
        """callback(key, value) runs after every change."""
        self._listeners.append(callback)

    def remember_recent(self, key, value, limit=32):
        recent = [value] + [entry for entry in self._values[key] if entry != value]
        self.set(key, recent[:limit])
