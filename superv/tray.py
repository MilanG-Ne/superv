"""Notification-area icon. Gtk.StatusIcon is deprecated in GTK 3 but it's the
one tray API the Xfce panel (and most X11 panels) support without extra
libraries."""

import warnings

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import APP_NAME  # noqa: E402


class Tray:
    def __init__(self, app):
        self.app = app
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self.icon = Gtk.StatusIcon.new_from_icon_name("superv")
            self.icon.set_title(APP_NAME)
            self.icon.set_tooltip_text("%s: clipboard history (Super+V)" % APP_NAME)
        self.icon.connect("activate", lambda _icon: app.toggle_popup())
        self.icon.connect("popup-menu", self._on_menu)
        self.menu = None

    def set_visible(self, visible):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self.icon.set_visible(visible)

    def _on_menu(self, icon, button, time):
        menu = Gtk.Menu()

        def item(label, callback):
            entry = Gtk.MenuItem(label=label)
            entry.connect("activate", lambda _e: callback())
            menu.append(entry)

        item("Clipboard history", lambda: self.app.show_popup("clipboard"))
        item("Emoji and symbols", lambda: self.app.show_popup("emoji"))
        menu.append(Gtk.SeparatorMenuItem())
        enabled = Gtk.CheckMenuItem(label="Save clipboard history")
        enabled.set_active(self.app.config["history_enabled"])
        enabled.connect("toggled", lambda e: self.app.config.set("history_enabled", e.get_active()))
        menu.append(enabled)
        item("Clear history", lambda: self.app.clear_history())
        item("Settings", self.app.open_settings)
        menu.append(Gtk.SeparatorMenuItem())
        item("Quit", self.app.quit)
        menu.show_all()
        self.menu = menu  # keep a reference while it's open
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            menu.popup(None, None, Gtk.StatusIcon.position_menu, icon, button, time)
