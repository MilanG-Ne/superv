"""The emoji, kaomoji and symbols tabs of the popup (Win+. / Win+V in Windows 11).

All three are the same widget: a grid of strings split into categories, a
category bar at the bottom, an optional search box, and a "recently used"
category. Picking a string types it into the app you were using.
"""

import json
import os
import unicodedata

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
EMOJI_FONT = "Noto Color Emoji, Segoe UI Emoji, Apple Color Emoji, Twemoji, EmojiOne Color, Symbola"
MAX_SEARCH_RESULTS = 160


def load_groups(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as handle:
        return json.load(handle)["groups"]


def _chars(*ranges):
    out = []
    for entry in ranges:
        if isinstance(entry, str):
            out.extend(entry)
        else:
            out.extend(chr(cp) for cp in range(entry[0], entry[1] + 1))
    seen, result = set(), []
    for char in out:
        if char in seen or unicodedata.category(char) in ("Cc", "Cf", "Cn", "Co", "Cs", "Zs", "Zl", "Zp", "Mn"):
            continue
        name = unicodedata.name(char, "")
        if name:
            seen.add(char)
            result.append([char, name.lower()])
    return result


def symbol_groups():
    """Unicode symbols, grouped roughly the way the Windows symbols tab does."""
    return [
        {"name": "Punctuation", "icon": "§",
         "items": _chars("¡¿§¶©®™°±·•…‰′″«»‹›‘’‚“”„†‡‖※‽", (0x2010, 0x2027), (0x2030, 0x205E))},
        {"name": "Currency", "icon": "€", "items": _chars("$¢£¤¥", (0x20A0, 0x20C0))},
        {"name": "Latin", "icon": "À", "items": _chars((0x00C0, 0x00D6), (0x00D8, 0x00F6), (0x00F8, 0x017F))},
        {"name": "Language", "icon": "Ω", "items": _chars((0x0391, 0x03A9), (0x03B1, 0x03C9), (0x0400, 0x045F))},
        {"name": "Math", "icon": "∑", "items": _chars("+−×÷=≠±∓<>≤≥¹²³¼½¾⅓⅔⅛⅜⅝⅞", (0x2070, 0x209C), (0x2200, 0x22FF))},
        {"name": "Arrows", "icon": "→", "items": _chars((0x2190, 0x21FF), (0x27F0, 0x27FF))},
        {"name": "Geometric", "icon": "■", "items": _chars((0x25A0, 0x25FF))},
        {"name": "Technical", "icon": "⌘", "items": _chars((0x2300, 0x23FF))},
        {"name": "Other symbols", "icon": "☆", "items": _chars((0x2600, 0x26FF), (0x2700, 0x27BF))},
    ]


def forward_typing(search, event):
    """Type-to-search: send printable keys typed elsewhere to the search box.
    Only printable keys and BackSpace, since SearchEntry.handle_event would
    also run Return through the entry and trigger its "activate"."""
    state = event.state & Gtk.accelerator_get_default_mod_mask()
    if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK | Gdk.ModifierType.SUPER_MASK):
        return False
    char = chr(Gdk.keyval_to_unicode(event.keyval) or 0)
    if event.keyval != Gdk.KEY_BackSpace and not (char.isprintable() and char != "\0" and not char.isspace()):
        return False
    if search.handle_event(event):
        search.grab_focus_without_selecting()
        return True
    return False


class PickerPage(Gtk.Box):
    """kind: 'emoji', 'kaomoji' or 'symbols'."""

    COLUMNS = {"emoji": 8, "kaomoji": 3, "symbols": 8}

    def __init__(self, app, kind, groups, title, search_placeholder=None, check_glyphs=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.app = app
        self.kind = kind
        self.recent_key = "recent_" + kind
        self.columns = self.COLUMNS[kind]
        self.check_glyphs = check_glyphs
        self.groups = groups
        self._built = {}
        self._probe = None
        self._reference_width = 0
        self._glyph_cache = {}
        self._current = 0
        self.get_style_context().add_class("picker-page")

        header = Gtk.Box(spacing=6)
        heading = Gtk.Label(label=title, xalign=0)
        heading.get_style_context().add_class("page-title")
        header.pack_start(heading, True, True, 0)
        self.pack_start(header, False, False, 0)

        self.search = None
        if search_placeholder:
            self.search = Gtk.SearchEntry(placeholder_text=search_placeholder)
            self.search.connect("search-changed", self._on_search)
            self.search.connect("activate", self._on_search_activate)
            self.search.connect("key-press-event", self._on_search_key)
            self.pack_start(self.search, False, False, 0)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)
        self.stack.set_vexpand(True)
        self.pack_start(self.stack, True, True, 0)

        self.empty_label = Gtk.Label(label="Nothing here yet.")
        self.empty_label.get_style_context().add_class("dim-label")
        self.stack.add_named(self.empty_label, "empty")

        self.results_scroll, self.results = self._make_grid()
        self.stack.add_named(self.results_scroll, "results")

        bar_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                        vscrollbar_policy=Gtk.PolicyType.NEVER)
        bar_scroll.get_style_context().add_class("category-scroll")
        self.bar = Gtk.Box(spacing=2)
        self.bar.get_style_context().add_class("category-bar")
        bar_scroll.add(self.bar)
        self.pack_end(bar_scroll, False, False, 0)
        self._buttons = []
        first = None
        for index, group in enumerate([{"name": "Recently used", "icon": None}] + groups):
            button = Gtk.RadioButton.new_from_widget(first)
            button.set_mode(False)
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_tooltip_text(group["name"])
            button.set_can_focus(False)
            if group["icon"] is None:
                button.add(Gtk.Image.new_from_icon_name("superv-recent-symbolic", Gtk.IconSize.MENU))
            else:
                label = Gtk.Label(label=group["icon"])
                if kind == "emoji":
                    label.get_style_context().add_class("emoji-icon")
                button.add(label)
            button.connect("toggled", self._on_category, index - 1)
            self.bar.pack_start(button, False, False, 0)
            self._buttons.append(button)
            first = first or button

    # ---- grids -------------------------------------------------------------

    def _make_grid(self):
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        grid = Gtk.FlowBox(homogeneous=True, selection_mode=Gtk.SelectionMode.NONE,
                           activate_on_single_click=True, valign=Gtk.Align.START)
        grid.set_min_children_per_line(self.columns)
        grid.set_max_children_per_line(self.columns)
        grid.get_style_context().add_class("picker-grid")
        grid.connect("child-activated", self._on_activated)
        grid.connect("key-press-event", self._on_grid_key)
        scroll.add(grid)
        return scroll, grid

    def _fill(self, grid, entries):
        for child in grid.get_children():
            grid.remove(child)
        for text, name in entries:
            label = Gtk.Label(label=text)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.get_style_context().add_class("%s-cell" % self.kind)
            child = Gtk.FlowBoxChild()
            child.add(label)
            if name:
                child.set_tooltip_text(name)
            child.value = text
            grid.add(child)
        grid.show_all()

    def _renderable(self, text):
        """False for emoji the installed fonts can't draw (boxes, or a ZWJ
        sequence falling apart into several pictures)."""
        if not self.check_glyphs:
            return True
        if text not in self._glyph_cache:
            self._glyph_cache[text] = self._measure(text)
        return self._glyph_cache[text]

    def _measure(self, text):
        if self._probe is None:
            self._probe = self.create_pango_layout("")
            self._probe.set_font_description(Pango.FontDescription.from_string(EMOJI_FONT + " 20px"))
            self._probe.set_text("😀", -1)
            self._reference_width = max(1, self._probe.get_pixel_size()[0])
        self._probe.set_text(text, -1)
        if self._probe.get_unknown_glyphs_count():
            return False
        return len(text) == 1 or self._probe.get_pixel_size()[0] <= self._reference_width * 1.5

    def _entries(self, index):
        if index < 0:
            known = {entry[0]: entry[1] for group in self.groups for entry in group["items"]}
            return [[text, known.get(text, "")] for text in self.app.config[self.recent_key]]
        return [[entry[0], entry[1]] for entry in self.groups[index]["items"] if self._renderable(entry[0])]

    def _show_group(self, index):
        self._current = index
        entries = self._entries(index)
        if not entries:
            self.empty_label.set_text("Things you use will show up here." if index < 0 else "Your fonts can't show these yet.")
            self.stack.set_visible_child_name("empty")
            return
        name = "group-%d" % index
        if index < 0 or name not in self._built:
            if name not in self._built:
                self._built[name] = self._make_grid()
                self.stack.add_named(self._built[name][0], name)
                self._built[name][0].show_all()
            self._fill(self._built[name][1], entries)
        self.stack.set_visible_child_name(name)
        self._built[name][0].get_vadjustment().set_value(0)

    def _hide_unrenderable_categories(self):
        for index, group in enumerate(self.groups):
            if not any(self._renderable(entry[0]) for entry in group["items"]):
                self._buttons[index + 1].hide()

    # ---- events ------------------------------------------------------------

    def _on_category(self, button, index):
        if button.get_active():
            if self.search and self.search.get_text():
                self.search.set_text("")
            self._show_group(index)

    def _on_activated(self, _grid, child):
        text = child.value
        self.app.config.remember_recent(self.recent_key, text)
        self.app.insert_text(text)

    @staticmethod
    def _rank(query, text, name):
        """Best matches first: the exact name, then names starting with the
        query as a word (so "euro" finds € before ₠), then any word, then the rest."""
        if text == query or name == query:
            return 0
        words = name.split()
        if words and words[0] == query:
            return 1
        if query in words:
            return 2
        return 3 if name.startswith(query) else 4

    def _on_search(self, entry):
        query = entry.get_text().strip().lower()
        if not query:
            self._show_group(self._current)
            return
        words = query.split()
        matches = []
        for group in self.groups:
            for item in group["items"]:
                text, name = item[0], item[1]
                if (text == query or all(word in name for word in words)) and self._renderable(text):
                    matches.append((self._rank(query, text, name), len(matches), text, name))
        matches.sort()
        if matches:
            self._fill(self.results, [[text, name] for _rank, _order, text, name in matches[:MAX_SEARCH_RESULTS]])
            self.stack.set_visible_child_name("results")
        else:
            self.empty_label.set_text("No results for “%s”." % entry.get_text().strip())
            self.stack.set_visible_child_name("empty")

    def _visible_grid(self):
        name = self.stack.get_visible_child_name()
        if name == "results":
            return self.results
        if name in self._built:
            return self._built[name][1]
        return None

    def _first_child(self):
        grid = self._visible_grid()
        return grid.get_child_at_index(0) if grid else None

    def _on_search_activate(self, entry):
        self._on_search(entry)  # "search-changed" lags 150 ms behind typing
        child = self._first_child()
        if child:
            self._on_activated(None, child)

    def _on_search_key(self, entry, event):
        if event.keyval == Gdk.KEY_Down:
            self._on_search(entry)
            child = self._first_child()
            if child:
                child.grab_focus()
            return True
        return False

    def _on_grid_key(self, grid, event):
        if self.search and forward_typing(self.search, event):
            return True
        if event.keyval == Gdk.KEY_Up and self.search:
            focus = self.get_toplevel().get_focus()
            if isinstance(focus, Gtk.FlowBoxChild) and focus.get_index() < self.columns:
                self.search.grab_focus_without_selecting()
                return True
        return False

    # ---- popup hooks -------------------------------------------------------

    def on_show(self):
        if self.search:
            self.search.set_text("")
        if not getattr(self, "_categories_checked", False):
            self._categories_checked = True
            self._hide_unrenderable_categories()
        start = 0 if self.app.config[self.recent_key] else 1
        if self._buttons[start].get_active():
            self._show_group(start - 1)
        else:
            self._buttons[start].set_active(True)
        GLib.idle_add(self.focus_default)

    def focus_default(self):
        if self.search:
            self.search.grab_focus()
        else:
            child = self._first_child()
            if child:
                child.grab_focus()
        return False

    def handle_key(self, event):
        return False
