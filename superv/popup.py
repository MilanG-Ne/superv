"""The Super+V panel: clipboard history plus emoji, kaomoji and symbols tabs."""

import os

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GdkPixbuf, GdkX11, GLib, Gtk, Pango  # noqa: E402

from . import pickers  # noqa: E402
from .pickers import forward_typing  # noqa: E402
from .monitor import RICH_TARGETS  # noqa: E402
from .store import KIND_FILES, KIND_IMAGE  # noqa: E402

WIDTH = 380
HEIGHT = 480
THUMB_WIDTH = 250
THUMB_HEIGHT = 110
PREVIEW_CHARS = 600
PREVIEW_LINES = 4
TABS = ("emoji", "kaomoji", "symbols", "clipboard")


def preview_text(text):
    lines = text.strip("\n")[:PREVIEW_CHARS].splitlines()[:PREVIEW_LINES]
    return "\n".join(line.expandtabs(4).rstrip()[:200] for line in lines)


def _icon(name, size=Gtk.IconSize.MENU):
    return Gtk.Image.new_from_icon_name(name, size)


class ClipRow(Gtk.ListBoxRow):
    def __init__(self, page, item):
        super().__init__()
        self.page = page
        self.item_id = item.id
        self.search_text = item.text[:20000].lower() if item.kind != KIND_IMAGE else ""
        self.get_style_context().add_class("clip-row")

        card = Gtk.Box(spacing=6)
        card.get_style_context().add_class("clip-card")
        self.add(card)

        card.pack_start(self._content(item), True, True, 0)

        actions = Gtk.Box(spacing=0, valign=Gtk.Align.START)
        pin = Gtk.Button(relief=Gtk.ReliefStyle.NONE, can_focus=False)
        pin.add(_icon("superv-pinned-symbolic" if item.pinned else "superv-pin-symbolic"))
        pin.set_tooltip_text("Unpin" if item.pinned else "Pin")
        pin.get_style_context().add_class("clip-action")
        if item.pinned:
            pin.get_style_context().add_class("pinned")
        pin.connect("clicked", lambda _b: page.app.set_pinned(self.item_id, not item.pinned))
        actions.pack_start(pin, False, False, 0)

        self.more = Gtk.MenuButton(relief=Gtk.ReliefStyle.NONE, can_focus=False)
        self.more.add(_icon("superv-more-symbolic"))
        self.more.set_tooltip_text("See more")
        self.more.get_style_context().add_class("clip-action")
        self.more.set_popover(self._menu(item))
        actions.pack_start(self.more, False, False, 0)
        card.pack_end(actions, False, False, 0)

    def _content(self, item):
        if item.kind == KIND_IMAGE:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.START)
            thumb = self.page.thumbnail(item)
            box.pack_start(Gtk.Image.new_from_pixbuf(thumb) if thumb else _icon("image-x-generic", Gtk.IconSize.DIALOG),
                           False, False, 0)
            if item.width:
                meta = Gtk.Label(label="%d × %d" % (item.width, item.height), xalign=0)
                meta.get_style_context().add_class("clip-meta")
                box.pack_start(meta, False, False, 0)
            return box
        if item.kind == KIND_FILES:
            names = [os.path.basename(path.rstrip("/")) or path for path in item.text.splitlines()]
            shown = "\n".join(names[:3])
            if len(names) > 3:
                shown += "\n+ %d more" % (len(names) - 3)
            box = Gtk.Box(spacing=8)
            box.pack_start(_icon("superv-files-symbolic", Gtk.IconSize.LARGE_TOOLBAR), False, False, 0)
            label = Gtk.Label(label=shown, xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            label.set_max_width_chars(8)  # small natural width; it still fills the card
            box.pack_start(label, True, True, 0)
            return box
        label = Gtk.Label(label=preview_text(item.text), xalign=0, yalign=0)
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_lines(PREVIEW_LINES)
        # Keep the natural width small so the panel stays WIDTH wide; the label
        # still wraps to the full width of the card.
        label.set_max_width_chars(8)
        label.set_width_chars(8)
        label.get_style_context().add_class("clip-text")
        return label

    def _menu(self, item):
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin=6)
        app = self.page.app

        def entry(text, callback):
            button = Gtk.ModelButton(text=text)
            button.connect("clicked", lambda _b: callback())
            box.pack_start(button, False, False, 0)

        auto = app.config["auto_paste"]
        entry("Paste" if auto else "Copy", lambda: app.use_item(self.item_id))
        if item.kind != KIND_IMAGE and any(t in item.formats for t in RICH_TARGETS + ("text/uri-list",)):
            entry("Paste as plain text" if auto else "Copy as plain text", lambda: app.use_item(self.item_id, plain=True))
        if auto:
            entry("Copy without pasting", lambda: app.use_item(self.item_id, paste=False))
        entry("Unpin" if item.pinned else "Pin", lambda: app.set_pinned(self.item_id, not item.pinned))
        entry("Delete", lambda: app.delete_item(self.item_id))
        box.show_all()
        popover.add(box)
        return popover


class ClipboardPage(Gtk.Box):
    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.app = app
        self._thumbs = {}
        self._dirty = True

        header = Gtk.Box(spacing=4)
        title = Gtk.Label(label="Clipboard", xalign=0)
        title.get_style_context().add_class("page-title")
        header.pack_start(title, True, True, 0)
        settings = Gtk.Button(relief=Gtk.ReliefStyle.NONE, tooltip_text="Settings")
        settings.add(_icon("superv-settings-symbolic"))
        settings.connect("clicked", lambda _b: app.open_settings())
        header.pack_start(settings, False, False, 0)
        self.clear_button = Gtk.Button(label="Clear all", relief=Gtk.ReliefStyle.NONE,
                                       tooltip_text="Remove everything except pinned items")
        self.clear_button.connect("clicked", lambda _b: app.clear_history())
        header.pack_start(self.clear_button, False, False, 0)
        self.pack_start(header, False, False, 0)

        self.search = Gtk.SearchEntry(placeholder_text="Search clipboard")
        self.search.connect("search-changed", lambda _e: self._refilter())
        self.search.connect("activate", self._on_search_activate)
        self.search.connect("key-press-event", self._on_search_key)
        self.pack_start(self.search, False, False, 0)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, vexpand=True)
        self.pack_start(self.stack, True, True, 0)

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, activate_on_single_click=True)
        self.list.get_style_context().add_class("clip-list")
        self.list.set_filter_func(self._filter)
        self.list.connect("row-activated", lambda _l, row: app.use_item(row.item_id))
        self.list.connect("key-press-event", self._on_list_key)
        self.list.connect("keynav-failed", self._on_keynav_failed)
        self.list.set_focus_vadjustment(scroll.get_vadjustment())
        scroll.add(self.list)
        self.stack.add_named(scroll, "list")

        self.stack.add_named(self._message("superv-clipboard-symbolic",
                                           "You'll see your clipboard history here once you've copied something."), "empty")
        self.stack.add_named(self._message("edit-find-symbolic", "No matches."), "nomatch")
        off = self._message("superv-clipboard-symbolic", "Can't show history",
                            "To see your clipboard history, turn it on.")
        turn_on = Gtk.Button(label="Turn on", halign=Gtk.Align.CENTER)
        turn_on.get_style_context().add_class("suggested-action")
        turn_on.connect("clicked", lambda _b: app.config.set("history_enabled", True))
        off.pack_start(turn_on, False, False, 6)
        self.stack.add_named(off, "off")

    def _message(self, icon, title, body=None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER, margin=24)
        image = _icon(icon, Gtk.IconSize.DIALOG)
        image.get_style_context().add_class("dim-label")
        box.pack_start(image, False, False, 0)
        heading = Gtk.Label(label=title, justify=Gtk.Justification.CENTER, wrap=True, max_width_chars=30)
        heading.get_style_context().add_class("empty-title" if body else "dim-label")
        box.pack_start(heading, False, False, 0)
        if body:
            text = Gtk.Label(label=body, justify=Gtk.Justification.CENTER, wrap=True, max_width_chars=30)
            text.get_style_context().add_class("dim-label")
            box.pack_start(text, False, False, 0)
        return box

    # ---- content -----------------------------------------------------------

    def thumbnail(self, item):
        if item.id not in self._thumbs:
            path = self.app.store.blob_path(item, "image/png")
            try:
                self._thumbs[item.id] = (GdkPixbuf.Pixbuf.new_from_file_at_scale(path, THUMB_WIDTH, THUMB_HEIGHT, True)
                                         if path else None)
            except GLib.Error:
                self._thumbs[item.id] = None
        return self._thumbs[item.id]

    def mark_dirty(self):
        self._dirty = True
        if self.get_mapped():
            self.rebuild()

    def rebuild(self):
        """Recreate the cards, keeping the selection on the same item, or on
        the same position if that item was just deleted."""
        self._dirty = False
        selected = self.list.get_selected_row()
        selected_id = selected.item_id if selected else None
        visible = self._visible_rows()
        position = visible.index(selected) if selected in visible else 0
        for row in self.list.get_children():
            self.list.remove(row)
        items = list(self.app.store.items)
        if self.app.config["pinned_first"]:
            items.sort(key=lambda item: not item.pinned)
        live = {item.id for item in items}
        self._thumbs = {key: value for key, value in self._thumbs.items() if key in live}
        for item in items:
            self.list.add(ClipRow(self, item))
        self.list.show_all()
        self._update_state()
        rows = self._visible_rows()
        match = [row for row in rows if row.item_id == selected_id]
        if match or rows:
            self.list.select_row(match[0] if match else rows[min(position, len(rows) - 1)])

    def _filter(self, row):
        query = self.search.get_text().strip().lower()
        return not query or query in row.search_text

    def _visible_rows(self):
        return [row for row in self.list.get_children() if row.get_child_visible()]

    def _refilter(self):
        self.list.invalidate_filter()
        self._update_state()
        rows = self._visible_rows()
        if rows:
            self.list.select_row(rows[0])

    def _update_state(self):
        if not self.app.config["history_enabled"]:
            self.stack.set_visible_child_name("off")
        elif not self.app.store.items:
            self.stack.set_visible_child_name("empty")
        elif not self._visible_rows():
            self.stack.set_visible_child_name("nomatch")
        else:
            self.stack.set_visible_child_name("list")
        self.clear_button.set_sensitive(self.app.config["history_enabled"] and any(not i.pinned for i in self.app.store.items))

    # ---- keyboard ----------------------------------------------------------

    def _on_search_activate(self, _entry):
        self._refilter()  # "search-changed" lags 150 ms behind typing
        rows = self._visible_rows()
        if rows:
            self.app.use_item(rows[0].item_id)

    def _on_search_key(self, _entry, event):
        if event.keyval == Gdk.KEY_Down:
            self._refilter()
            rows = self._visible_rows()
            if rows:
                self.list.select_row(rows[0])
                rows[0].grab_focus()
            return True
        return False

    def _on_keynav_failed(self, _list, direction):
        if direction == Gtk.DirectionType.UP:
            self.search.grab_focus_without_selecting()
            return True
        return False

    def _on_list_key(self, _list, event):
        row = self.list.get_focus_child() or self.list.get_selected_row()
        state = event.state & Gtk.accelerator_get_default_mod_mask()
        key = event.keyval
        if row is not None:
            if key == Gdk.KEY_Delete:
                self.app.delete_item(row.item_id)
                return True
            if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state == Gdk.ModifierType.SHIFT_MASK:
                self.app.use_item(row.item_id, plain=True)
                return True
            if key == Gdk.KEY_p and state == Gdk.ModifierType.CONTROL_MASK:
                item = self.app.store.get(row.item_id)
                if item:
                    self.app.set_pinned(item.id, not item.pinned)
                return True
            if key == Gdk.KEY_Menu or (key == Gdk.KEY_F10 and state == Gdk.ModifierType.SHIFT_MASK):
                row.more.set_active(True)
                return True
        return forward_typing(self.search, event)

    # ---- popup hooks ---------------------------------------------------------

    def on_show(self):
        self.search.set_text("")
        if self._dirty:
            self.rebuild()
        else:
            self._update_state()
        rows = self._visible_rows()
        if rows:
            self.list.select_row(rows[0])
            GLib.idle_add(self.focus_default)
        else:
            GLib.idle_add(self.search.grab_focus)

    def focus_default(self):
        rows = self._visible_rows()
        if rows:
            (self.list.get_selected_row() or rows[0]).grab_focus()
        return False

    def restore_focus_after_change(self):
        """After deleting or pinning, keep keyboard focus in the list."""
        rows = self._visible_rows()
        if rows and self.get_mapped():
            row = self.list.get_selected_row() or rows[0]
            self.list.select_row(row)
            row.grab_focus()


class Popup(Gtk.Window):
    def __init__(self, app):
        super().__init__(type=Gtk.WindowType.TOPLEVEL, title="SuperV")
        self.app = app
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_default_size(WIDTH, HEIGHT)
        self.set_size_request(WIDTH, HEIGHT)
        self.set_icon_name("superv")
        self.get_style_context().add_class("superv-popup")
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)
            self.get_style_context().add_class("composited")

        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        frame.get_style_context().add_class("superv-frame")
        self.add(frame)

        self.tabs = Gtk.Box(spacing=2)
        self.tabs.get_style_context().add_class("superv-tabs")
        frame.pack_start(self.tabs, False, False, 0)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=100)
        self.stack.get_style_context().add_class("superv-pages")
        frame.pack_start(self.stack, True, True, 0)

        self.pages = {
            "emoji": pickers.PickerPage(app, "emoji", pickers.load_groups("emoji.json"), "Emoji",
                                        "Search emoji", check_glyphs=True),
            "kaomoji": pickers.PickerPage(app, "kaomoji", pickers.load_groups("kaomoji.json"), "Kaomoji"),
            "symbols": pickers.PickerPage(app, "symbols", pickers.symbol_groups(), "Symbols", "Search symbols",
                                          check_glyphs=True),
            "clipboard": ClipboardPage(app),
        }
        self.buttons = {}
        labels = {"emoji": ("superv-emoji-symbolic", "Emoji  (Super+.)"), "kaomoji": (";-)", "Kaomoji"),
                  "symbols": ("Ω", "Symbols"), "clipboard": ("superv-clipboard-symbolic", "Clipboard history  (Super+V)")}
        first = None
        for name in TABS:
            self.stack.add_named(self.pages[name], name)
            button = Gtk.RadioButton.new_from_widget(first)
            button.set_mode(False)
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_can_focus(False)
            icon, tooltip = labels[name]
            button.add(_icon(icon) if icon.startswith("superv-") else Gtk.Label(label=icon))
            button.set_tooltip_text(tooltip)
            button.connect("toggled", self._on_tab, name)
            self.tabs.pack_start(button, False, False, 0)
            self.buttons[name] = button
            first = first or button

        self._shown = 0  # bumped on every open, so a stale focus-out can't close a newer panel
        self.connect("key-press-event", self._on_key)
        self.connect("focus-out-event", self._on_focus_out)
        self.connect("delete-event", self._on_delete)
        frame.show_all()
        self.realize()

    @property
    def current_tab(self):
        return self.stack.get_visible_child_name()

    def _on_tab(self, button, name):
        if button.get_active():
            self.stack.set_visible_child_name(name)
            self.pages[name].on_show()

    def open(self, tab=None):
        self._shown += 1
        tab = tab if tab in TABS else "clipboard"
        if self.buttons[tab].get_active():
            self.stack.set_visible_child_name(tab)
            self.pages[tab].on_show()
        else:
            self.buttons[tab].set_active(True)
        self._place()
        self.show()
        self.present_with_time(GdkX11.x11_get_server_time(self.get_window()))
        GLib.timeout_add(150, self._ensure_focus)

    def _ensure_focus(self):
        if self.get_visible() and not self.is_active():
            self.get_window().focus(GdkX11.x11_get_server_time(self.get_window()))
        return False

    def _place(self):
        display = Gdk.Display.get_default()
        _screen, px, py = display.get_default_seat().get_pointer().get_position()
        area = display.get_monitor_at_point(px, py).get_workarea()
        if self.app.config["popup_position"] == "center":
            x = area.x + (area.width - WIDTH) // 2
            y = area.y + (area.height - HEIGHT) // 2
        else:
            x = px + 12 if px + 12 + WIDTH <= area.x + area.width else px - WIDTH - 12
            y = py + 12 if py + 12 + HEIGHT <= area.y + area.height else py - HEIGHT - 12
        x = max(area.x, min(x, area.x + area.width - WIDTH))
        y = max(area.y, min(y, area.y + area.height - HEIGHT))
        self.move(x, y)

    def next_tab(self, step):
        index = (TABS.index(self.current_tab) + step) % len(TABS)
        self.buttons[TABS[index]].set_active(True)

    def _on_key(self, _widget, event):
        state = event.state & Gtk.accelerator_get_default_mod_mask()
        key = event.keyval
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        if key == Gdk.KEY_Escape:
            page = self.pages[self.current_tab]
            if page.search is not None and page.search.get_text():
                page.search.set_text("")
                page.focus_default()
            else:
                self.hide()
            return True
        if ctrl and key in (Gdk.KEY_Page_Down, Gdk.KEY_Tab):
            self.next_tab(1)
            return True
        if ctrl and key in (Gdk.KEY_Page_Up, Gdk.KEY_ISO_Left_Tab):
            self.next_tab(-1)
            return True
        return False

    def _on_focus_out(self, _widget, _event):
        # Like Windows: clicking anywhere else closes the panel. Wait a moment
        # so a popover or a quick focus bounce while opening doesn't close it.
        GLib.timeout_add(120, self._hide_if_inactive, self._shown)
        return False

    def _hide_if_inactive(self, shown):
        if shown == self._shown and self.get_visible() and not self.is_active():
            self.hide()
        return False

    def _on_delete(self, *_args):
        self.hide()
        return True
