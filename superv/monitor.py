"""Watches the X11 CLIPBOARD selection and serves history items back to it.

Capturing: when another app takes the clipboard, we ask it which formats it
offers and fetch the ones we keep (text, HTML/RTF, file lists, PNG).

Serving: to put a history item back on the clipboard we own the selection
through our own hidden widget and answer each paste request with the stored
bytes. That's why re-pasting a formatted snippet or a list of files from
history works like the original copy did.
"""

from html.parser import HTMLParser

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk  # noqa: E402

from .store import KIND_FILES, KIND_IMAGE, KIND_TEXT  # noqa: E402

TEXT_TARGETS = ("UTF8_STRING", "TEXT", "COMPOUND_TEXT", "STRING", "text/plain;charset=utf-8", "text/plain")
RICH_TARGETS = ("text/html", "text/rtf", "text/richtext")
FILE_TARGETS = ("x-special/gnome-copied-files", "text/uri-list")
IMAGE_TARGET = "image/png"
META_TARGETS = ("TARGETS", "TIMESTAMP", "MULTIPLE", "SAVE_TARGETS", "DELETE")
# KeePassXC, KDE apps and others set this on passwords they put on the clipboard.
SECRET_HINT = "x-kde-passwordManagerHint"

INFO_TEXT = 1
INFO_RAW = 2

CAPTURE_DELAY_MS = 60  # apps sometimes set the clipboard several times in a row


class _HtmlText(HTMLParser):
    BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, _attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(data):
    """Plain text for a clipboard that only offers HTML."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        markup = data.decode("utf-16", "replace")
    else:
        markup = data.decode("utf-8", "replace")
    parser = _HtmlText()
    parser.feed(markup)
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def _uri_list_to_paths(data):
    """Local paths from a text/uri-list or x-special/gnome-copied-files
    payload, or None if it isn't purely local files (e.g. a copied web link)."""
    paths = []
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line in ("copy", "cut"):
            continue
        try:
            path, _ = GLib.filename_from_uri(line)
        except GLib.Error:
            return None
        paths.append(path)
    return paths or None


class ClipboardMonitor:
    def __init__(self, store, config, selection=Gdk.SELECTION_CLIPBOARD):
        self.store = store
        self.config = config
        self.selection = selection
        self.clipboard = Gtk.Clipboard.get(selection)
        self.clipboard.connect("owner-change", self._on_owner_change)

        self._owner = Gtk.Invisible()
        self._owner.realize()
        self._owner_xid = self._owner.get_window().get_xid()
        self._owner.connect("selection-get", self._on_selection_get)
        self._owner.connect("selection-clear-event", self._on_selection_clear)

        self._serving = None  # (item_id, plain) or (None, text) for a one-off text
        self._external_id = None  # history item captured from the app that owns the clipboard now
        self._capture_timer = 0
        self._generation = 0
        self._skip_restore = False
        self._fetched_callback = None

    # ---- who owns the clipboard ------------------------------------------

    @property
    def current_item_id(self):
        """The history item that is on the clipboard right now, if we know it."""
        if self._serving and self._serving[0]:
            return self._serving[0]
        return self._external_id

    def _timestamp(self):
        return GdkX11.x11_get_server_time(self._owner.get_window())

    def _on_owner_change(self, clipboard, event):
        if event.owner is not None and event.owner.get_xid() == self._owner_xid:
            return
        self._external_id = None
        self._generation += 1  # drop results of a capture still in flight
        if self._capture_timer:
            GLib.source_remove(self._capture_timer)
        self._capture_timer = GLib.timeout_add(CAPTURE_DELAY_MS, self._capture)

    # ---- capturing ---------------------------------------------------------

    def _capture(self):
        self._capture_timer = 0
        self._generation += 1
        generation = self._generation
        self.clipboard.request_targets(lambda _cb, atoms, *_: self._on_targets(generation, atoms))
        return False

    def _on_targets(self, generation, atoms):
        if generation != self._generation:
            return
        names = [atom.name() for atom in atoms or []]
        if not [name for name in names if name not in META_TARGETS]:
            self._clipboard_emptied()
            return
        if not self.config["history_enabled"]:
            return
        if self.config["ignore_secrets"] and SECRET_HINT in names:
            return
        if any(target in names for target in FILE_TARGETS):
            self._fetch(generation, KIND_FILES, [t for t in FILE_TARGETS if t in names])
        elif any(target in names for target in TEXT_TARGETS):
            self._fetch(generation, KIND_TEXT, [t for t in RICH_TARGETS if t in names])
        elif self.config["capture_images"] and any(name.startswith("image/") for name in names):
            self._fetch(generation, KIND_IMAGE, [IMAGE_TARGET] if IMAGE_TARGET in names else [])
        elif "text/html" in names:
            self._fetch(generation, KIND_TEXT, ["text/html"])

    def _fetch(self, generation, kind, targets):
        """Fetch the wanted formats one after another, then store the item."""
        pending = list(targets)
        blobs = {}
        limit = self.config["max_item_mb"] * 1024 * 1024

        def next_target():
            if generation != self._generation:
                return
            if pending:
                target = pending.pop(0)
                self.clipboard.request_contents(Gdk.Atom.intern(target, False),
                                                lambda _cb, data, *_: received(target, data))
            elif kind == KIND_IMAGE:
                if IMAGE_TARGET in blobs:
                    self._store_image(generation, blobs[IMAGE_TARGET], limit)
                else:
                    self.clipboard.request_image(lambda _cb, pixbuf, *_: self._store_pixbuf(generation, pixbuf, limit))
            else:
                self.clipboard.request_text(lambda _cb, text, *_: self._store_text(generation, kind, text, blobs, limit))

        def received(target, data):
            raw = data.get_data() if data is not None and data.get_length() > 0 else None
            if raw:
                blobs[target] = bytes(raw)
            next_target()

        next_target()

    def _store_text(self, generation, kind, text, blobs, limit):
        if generation != self._generation:
            return
        if kind == KIND_FILES:
            listing = blobs.get("x-special/gnome-copied-files") or blobs.get("text/uri-list")
            paths = _uri_list_to_paths(listing) if listing else None
            if paths:
                text = "\n".join(paths)
            else:  # e.g. a browser's "Copy link": keep it as plain text
                if not text and listing:
                    lines = listing.decode("utf-8", "replace").splitlines()
                    text = "\n".join(line.strip() for line in lines if line.strip() and not line.startswith("#"))
                kind, blobs = KIND_TEXT, {}
        if not text and "text/html" in blobs:
            text = html_to_text(blobs["text/html"])
        if not text or not text.strip():
            return
        if len(text.encode("utf-8", "surrogatepass")) + sum(map(len, blobs.values())) > limit:
            return
        item = self.store.add(kind, text, blobs)
        self._external_id = item.id

    def _store_pixbuf(self, generation, pixbuf, limit):
        if generation != self._generation or pixbuf is None:
            return
        ok, png = pixbuf.save_to_bufferv("png", [], [])
        if ok:
            self._store_image(generation, bytes(png), limit, pixbuf.get_width(), pixbuf.get_height())

    def _store_image(self, generation, png, limit, width=0, height=0):
        if generation != self._generation or len(png) > limit:
            return
        item = self.store.add(KIND_IMAGE, "", {IMAGE_TARGET: png}, width, height)
        self._external_id = item.id

    def _clipboard_emptied(self):
        """On X11 the clipboard empties when the app you copied from quits.
        Put the most recent history item back, like Windows would still have it."""
        if self._skip_restore:
            self._skip_restore = False
            return
        if self._serving or not (self.config["restore_on_owner_exit"] and self.config["history_enabled"]):
            return
        newest = self.store.newest()
        if newest is not None:
            self.serve(newest)

    # ---- serving -----------------------------------------------------------

    def use(self, item, plain=False):
        """Make `item` the clipboard content. If it already is (still owned by
        the app it came from), leave it there: that app may offer formats we
        didn't keep."""
        if not plain and item.id == self._external_id:
            return True
        return self.serve(item, plain)

    def serve(self, item, plain=False):
        entries = []
        if item.kind in (KIND_TEXT, KIND_FILES):
            entries += [Gtk.TargetEntry.new(target, 0, INFO_TEXT) for target in TEXT_TARGETS]
        if not plain or item.kind == KIND_IMAGE:
            entries += [Gtk.TargetEntry.new(target, 0, INFO_RAW) for target in item.formats]
        return self._own(entries, (item.id, plain))

    def serve_text(self, text, on_fetched=None):
        """Put a one-off string (an emoji, a symbol) on the clipboard without
        recording it. `on_fetched` runs once an app has pasted it."""
        self._fetched_callback = on_fetched
        return self._own([Gtk.TargetEntry.new(t, 0, INFO_TEXT) for t in TEXT_TARGETS], (None, text))

    def serving_text(self, text):
        return self._serving == (None, text)

    def _own(self, entries, serving):
        Gtk.selection_clear_targets(self._owner, self.selection)
        if entries:
            Gtk.selection_add_targets(self._owner, self.selection, entries)
        if Gtk.selection_owner_set(self._owner, self.selection, self._timestamp()):
            self._serving = serving
            self._external_id = None
            return True
        self._serving = None
        return False

    def release(self):
        """Stop serving; the clipboard becomes empty and stays empty."""
        if self._serving is not None:
            self._serving = None
            self._skip_restore = True
            Gtk.selection_owner_set(None, self.selection, self._timestamp())

    def clear_current(self):
        """Empty the clipboard, whoever owns it (used by "Clear clipboard data")."""
        self._own([], (None, ""))
        self.release()

    def forget(self, item_id):
        """An item was deleted from history: stop offering it."""
        if self._serving and self._serving[0] == item_id:
            self.release()
        if self._external_id == item_id:
            self._external_id = None

    def _on_selection_get(self, _widget, data, info, _time):
        if self._serving is None:
            return
        item_id, extra = self._serving
        if item_id is None:
            data.set_text(extra, -1)
            if self._fetched_callback:
                callback, self._fetched_callback = self._fetched_callback, None
                GLib.idle_add(callback)
            return
        item = self.store.get(item_id)
        if item is None:
            return
        if info == INFO_TEXT:
            data.set_text(item.text, -1)
        else:
            raw = self.store.read_blob(item, data.get_target().name())
            if raw is not None:
                data.set(data.get_target(), 8, raw)

    def _on_selection_clear(self, _widget, _event):
        self._serving = None
        self._fetched_callback = None
        return False
