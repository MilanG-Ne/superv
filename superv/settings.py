"""Settings window, laid out like Windows Settings > System > Clipboard."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import APP_NAME, HOMEPAGE, __version__, desktop  # noqa: E402


class SettingsWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="%s Settings" % APP_NAME)
        self.app = app
        self.set_default_size(620, 720)
        self.set_icon_name("superv")
        self.set_position(Gtk.WindowPosition.CENTER)
        self.get_style_context().add_class("superv-settings")
        self._switches = {}

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.add(scroll)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=20)
        scroll.add(body)

        title = Gtk.Label(label="Clipboard", xalign=0)
        title.get_style_context().add_class("settings-title")
        body.pack_start(title, False, False, 0)

        body.pack_start(self._section([
            self._switch_row("Clipboard history",
                             "Save multiple items to the clipboard to use later. "
                             "Press Super+V to view your history and paste from it.",
                             "history_enabled"),
            self._clear_row(),
        ]), False, False, 0)

        self.max_items = self._spin("max_items", 1, 1000)
        self.max_mb = self._spin("max_item_mb", 1, 200)
        body.pack_start(self._heading("History"), False, False, 0)
        body.pack_start(self._section([
            self._row("Items to keep", "Older items drop off the list. Pinned items don't count.", self.max_items),
            self._row("Largest item to keep (MB)", "Bigger copies, such as huge screenshots, are skipped.", self.max_mb),
            self._switch_row("Save images", "Keep screenshots and copied pictures in the history.", "capture_images"),
            self._switch_row("Keep unpinned items after a restart",
                             "Windows keeps only pinned items. Turn this on to keep the whole history.",
                             "keep_history_after_restart"),
            self._switch_row("Show pinned items first", "Keep pinned items at the top of the list.", "pinned_first"),
        ]), False, False, 0)

        body.pack_start(self._heading("Pasting"), False, False, 0)
        position = Gtk.ComboBoxText(valign=Gtk.Align.CENTER)
        position.append("pointer", "Next to the mouse pointer")
        position.append("center", "Center of the screen")
        position.set_active_id(app.config["popup_position"])
        position.connect("changed", lambda combo: app.config.set("popup_position", combo.get_active_id()))
        body.pack_start(self._section([
            self._switch_row("Paste automatically",
                             "Paste into the app you were using as soon as you pick an item. Turn off to only copy it.",
                             "auto_paste"),
            self._switch_row("Keep the clipboard when an app closes",
                             "On Linux the clipboard empties when the app you copied from quits. SuperV puts the last item back.",
                             "restore_on_owner_exit"),
            self._row("Open the panel", None, position),
        ]), False, False, 0)

        body.pack_start(self._heading("Privacy"), False, False, 0)
        body.pack_start(self._section([
            self._switch_row("Ignore passwords",
                             "Don't save items that password managers such as KeePassXC mark as secret.",
                             "ignore_secrets"),
        ]), False, False, 0)

        body.pack_start(self._heading("Startup and shortcuts"), False, False, 0)
        self.shortcut_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.shortcut_label.get_style_context().add_class("dim-label")
        self.shortcut_button = Gtk.Button(valign=Gtk.Align.CENTER)
        self.shortcut_button.connect("clicked", self._on_shortcut_button)
        autostart = Gtk.Switch(valign=Gtk.Align.CENTER, active=desktop.autostart_enabled())
        autostart.connect("notify::active", lambda sw, _p: desktop.set_autostart(sw.get_active()))
        body.pack_start(self._section([
            self._row("Keyboard shortcuts", self.shortcut_label, self.shortcut_button),
            self._row("Start at login", "Run SuperV in the background when you log in.", autostart),
            self._switch_row("Show icon in the notification area", None, "show_tray_icon"),
        ]), False, False, 0)

        about = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=12)
        version = Gtk.Label(label="%s %s. Free and open source (MIT license)." % (APP_NAME, __version__), xalign=0)
        version.get_style_context().add_class("dim-label")
        about.pack_start(version, False, False, 0)
        link = Gtk.LinkButton(uri=HOMEPAGE, label="Source code, bug reports and help", halign=Gtk.Align.START)
        about.pack_start(link, False, False, 0)
        body.pack_start(about, False, False, 0)

        self.connect("delete-event", self._on_delete)
        app.config.connect(self._on_config_changed)
        self._refresh_shortcuts()
        scroll.show_all()

    # ---- building blocks ------------------------------------------------------

    def _heading(self, text):
        label = Gtk.Label(label=text, xalign=0, margin_top=14)
        label.get_style_context().add_class("settings-heading")
        return label

    def _section(self, rows):
        box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        box.get_style_context().add_class("frame")
        box.get_style_context().add_class("settings-section")
        for row in rows:
            box.add(row)
        return box

    def _row(self, title, subtitle, control):
        row = Gtk.ListBoxRow(activatable=False)
        box = Gtk.Box(spacing=16, margin=12)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        heading = Gtk.Label(label=title, xalign=0, wrap=True)
        texts.pack_start(heading, False, False, 0)
        if isinstance(subtitle, Gtk.Widget):
            texts.pack_start(subtitle, False, False, 0)
        elif subtitle:
            sub = Gtk.Label(label=subtitle, xalign=0, wrap=True, max_width_chars=60)
            sub.get_style_context().add_class("dim-label")
            texts.pack_start(sub, False, False, 0)
        box.pack_start(texts, True, True, 0)
        box.pack_end(control, False, False, 0)
        row.add(box)
        return row

    def _switch_row(self, title, subtitle, key):
        switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=self.app.config[key])
        switch.connect("notify::active", lambda sw, _p: self.app.config.set(key, sw.get_active()))
        self._switches[key] = switch
        return self._row(title, subtitle, switch)

    def _spin(self, key, low, high):
        spin = Gtk.SpinButton.new_with_range(low, high, 1)
        spin.set_valign(Gtk.Align.CENTER)
        spin.set_value(self.app.config[key])
        spin.connect("value-changed", lambda s: self.app.config.set(key, s.get_value_as_int()))
        return spin

    def _clear_row(self):
        box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        done = Gtk.Label(label="Cleared")
        done.get_style_context().add_class("dim-label")
        done.set_no_show_all(True)
        button = Gtk.Button(label="Clear")

        def clear(_button):
            self.app.clear_history(clear_clipboard=True)
            done.show()
            GLib.timeout_add_seconds(3, lambda: done.hide() or False)

        button.connect("clicked", clear)
        box.pack_start(done, False, False, 0)
        box.pack_start(button, False, False, 0)
        return self._row("Clear clipboard data", "Clears everything on this device except pinned items.", box)

    # ---- state -----------------------------------------------------------------

    def _refresh_shortcuts(self):
        if not desktop.is_xfce():
            self.shortcut_label.set_text(desktop.manual_shortcut_hint())
            self.shortcut_button.hide()
            self.shortcut_button.set_no_show_all(True)
            return
        status = desktop.shortcut_status()
        ours = [desktop.pretty_shortcut(key) for key, (_value, mine) in status.items() if mine]
        taken = [(desktop.pretty_shortcut(key), value) for key, (value, mine) in status.items() if value and not mine]
        lines = []
        if ours:
            lines.append("Active: %s opens the clipboard, Super+. opens emoji." % " and ".join(ours)
                         if len(ours) == 2 else "Active: %s." % ours[0])
        else:
            lines.append("Super+V and Super+. are not set up yet.")
        for key, value in taken:
            lines.append("%s is already used by “%s”. Change it in Settings > Keyboard." % (key, value))
        self.shortcut_label.set_text("\n".join(lines))
        self.shortcut_button.set_label("Remove" if ours else "Set up")
        self.shortcut_button.set_sensitive(bool(ours) or len(taken) < len(status))

    def _on_shortcut_button(self, _button):
        if any(mine for _value, mine in desktop.shortcut_status().values()):
            desktop.remove_shortcuts()
        else:
            desktop.setup_shortcuts()
        self._refresh_shortcuts()

    def _on_config_changed(self, key, value):
        switch = self._switches.get(key)
        if switch is not None and switch.get_active() != value:
            switch.set_active(value)
        if key == "max_items" and self.max_items.get_value_as_int() != value:
            self.max_items.set_value(value)

    def _on_delete(self, *_args):
        self.hide()
        return True

    def present_now(self):
        self._refresh_shortcuts()
        self.show()
        self.present()
