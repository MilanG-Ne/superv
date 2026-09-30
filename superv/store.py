"""Clipboard history: the list of items and their copies on disk.

Pinned items (and the whole history, if the user turns that on) live in the
data directory and survive a reboot. Everything else lives in the runtime
directory, which the system empties on logout and reboot. So by default
SuperV behaves like Windows: after a restart only pinned items are left.

Plain text is kept in the index. Anything else (HTML, file lists, PNG images)
is kept as a "blob": the raw bytes of one clipboard format, so it can be
served back exactly as the original app offered it.

No GTK in here; the unit tests exercise this module without a display.
"""

import hashlib
import json
import os
import shutil
import struct
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from urllib.parse import quote

from . import paths

KIND_TEXT = "text"
KIND_IMAGE = "image"
KIND_FILES = "files"

INDEX_VERSION = 1
PERSISTENT_INDEX = "history.json"
SESSION_INDEX = "session.json"


@dataclass
class Item:
    id: str
    kind: str
    text: str = ""
    formats: list = field(default_factory=list)  # clipboard targets stored as blobs
    pinned: bool = False
    created: float = 0.0
    digest: str = ""
    size: int = 0
    width: int = 0
    height: int = 0

    @classmethod
    def from_dict(cls, data):
        known = {f.name for f in fields(cls)}
        item = cls(**{key: value for key, value in data.items() if key in known})
        if not isinstance(item.id, str) or not item.id.isalnum():
            raise ValueError("bad item id")
        if item.kind not in (KIND_TEXT, KIND_IMAGE, KIND_FILES):
            raise ValueError("bad item kind")
        return item


def digest_for(kind, text, blobs):
    sha = hashlib.sha1(kind.encode())
    if kind == KIND_IMAGE:
        sha.update(blobs.get("image/png", b""))
    else:
        sha.update(text.encode("utf-8", "surrogatepass"))
    return sha.hexdigest()


def png_size(data):
    """Width and height from a PNG header, without decoding the image."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    return 0, 0


def boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _blob_name(item_id, target):
    return "%s.%s" % (item_id, quote(target, safe=""))


class Store:
    def __init__(self, data_dir=None, runtime_dir=None, max_items=25, persist_all=False):
        self.data_dir = data_dir or paths.data_dir()
        self.runtime_dir = runtime_dir or paths.runtime_dir()
        self.max_items = max_items
        self.persist_all = persist_all
        self.items = []  # newest first
        self._listeners = []
        self._last_persistent = None

    # ---- listeners -------------------------------------------------------

    def connect(self, callback):
        """callback() runs after every change to the list."""
        self._listeners.append(callback)

    def _changed(self):
        self.save()
        for callback in list(self._listeners):
            callback()

    # ---- blobs -----------------------------------------------------------

    def _blob_dirs(self):
        return os.path.join(self.data_dir, "blobs"), os.path.join(self.runtime_dir, "blobs")

    def is_persistent(self, item):
        return item.pinned or self.persist_all

    def _home_dir(self, item):
        persistent, session = self._blob_dirs()
        return persistent if self.is_persistent(item) else session

    def blob_path(self, item, target):
        name = _blob_name(item.id, target)
        for directory in self._blob_dirs():
            candidate = os.path.join(directory, name)
            if os.path.exists(candidate):
                return candidate
        return None

    def read_blob(self, item, target):
        if target not in item.formats:
            return None
        path = self.blob_path(item, target)
        if path is None:
            return None
        try:
            with open(path, "rb") as handle:
                return handle.read()
        except OSError:
            return None

    def _write_blobs(self, item, blobs):
        directory = paths.ensure_dir(self._home_dir(item))
        for target, data in blobs.items():
            paths.write_atomic(os.path.join(directory, _blob_name(item.id, target)), data)

    def _delete_blobs(self, item, targets=None):
        for target in item.formats if targets is None else targets:
            name = _blob_name(item.id, target)
            for directory in self._blob_dirs():
                try:
                    os.unlink(os.path.join(directory, name))
                except FileNotFoundError:
                    pass

    def _relocate(self, item):
        """Move an item's blobs to the directory that matches its pinned state."""
        home = self._home_dir(item)
        for target in item.formats:
            name = _blob_name(item.id, target)
            wanted = os.path.join(home, name)
            if os.path.exists(wanted):
                continue
            current = self.blob_path(item, target)
            if current:
                paths.ensure_dir(home)
                shutil.move(current, wanted)

    # ---- load / save -----------------------------------------------------

    def _read_index(self, path):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return None, []
        except (OSError, ValueError) as error:
            print("superv: ignoring unreadable %s: %s" % (path, error), file=sys.stderr)
            return None, []
        items = []
        for entry in data.get("items", []) if isinstance(data, dict) else []:
            try:
                items.append(Item.from_dict(entry))
            except (TypeError, ValueError):
                continue
        return data.get("boot_id"), items

    def load(self):
        _, persistent = self._read_index(os.path.join(self.data_dir, PERSISTENT_INDEX))
        session_boot, session = self._read_index(os.path.join(self.runtime_dir, SESSION_INDEX))
        if session and session_boot != boot_id():
            # The runtime dir survived a reboot (no tmpfs); treat it as gone.
            session = []
        known = {item.id for item in session}
        merged = session + [item for item in persistent if item.id not in known]
        if not session and not self.persist_all:
            merged = [item for item in merged if item.pinned]
        self.items = [item for item in merged if self._blobs_present(item)]
        self._enforce_limit()
        self._remove_orphans()
        self.save()

    def _blobs_present(self, item):
        return all(self.blob_path(item, target) for target in item.formats)

    def _remove_orphans(self):
        wanted = {_blob_name(item.id, target) for item in self.items for target in item.formats}
        for directory in self._blob_dirs():
            try:
                names = os.listdir(directory)
            except FileNotFoundError:
                continue
            for name in names:
                if name not in wanted:
                    try:
                        os.unlink(os.path.join(directory, name))
                    except OSError:
                        pass

    def save(self):
        for item in self.items:
            self._relocate(item)
        session = {"version": INDEX_VERSION, "boot_id": boot_id(),
                   "items": [asdict(item) for item in self.items]}
        paths.ensure_dir(self.runtime_dir)
        paths.write_atomic(os.path.join(self.runtime_dir, SESSION_INDEX),
                           json.dumps(session, ensure_ascii=False).encode("utf-8"))
        persistent = json.dumps({"version": INDEX_VERSION,
                                 "items": [asdict(item) for item in self.items if self.is_persistent(item)]},
                                ensure_ascii=False)
        if persistent != self._last_persistent:
            paths.ensure_dir(self.data_dir)
            paths.write_atomic(os.path.join(self.data_dir, PERSISTENT_INDEX),
                               persistent.encode("utf-8"), sync=True)
            self._last_persistent = persistent

    # ---- queries ---------------------------------------------------------

    def get(self, item_id):
        for item in self.items:
            if item.id == item_id:
                return item
        return None

    def newest(self):
        return self.items[0] if self.items else None

    # ---- changes ---------------------------------------------------------

    def add(self, kind, text="", blobs=None, width=0, height=0):
        """Record a new clipboard entry. Copying the same thing again moves
        the existing entry to the top instead of adding a duplicate."""
        blobs = dict(blobs or {})
        digest = digest_for(kind, text, blobs)
        existing = next((item for item in self.items if item.digest == digest), None)
        if existing is not None:
            if blobs or existing.formats:
                # Same content copied again: keep the formats of the latest copy.
                self._delete_blobs(existing)
                existing.formats = list(blobs)
                self._write_blobs(existing, blobs)
                existing.size = self._size(text, blobs)
            self.items.remove(existing)
            self.items.insert(0, existing)
            self._changed()
            return existing
        if kind == KIND_IMAGE and not width:
            width, height = png_size(blobs.get("image/png", b""))
        item = Item(id=uuid.uuid4().hex, kind=kind, text=text, formats=list(blobs),
                    created=time.time(), digest=digest, size=self._size(text, blobs),
                    width=width, height=height)
        self._write_blobs(item, blobs)
        self.items.insert(0, item)
        self._enforce_limit()
        self._changed()
        return item

    @staticmethod
    def _size(text, blobs):
        return len(text.encode("utf-8", "surrogatepass")) + sum(len(b) for b in blobs.values())

    def touch(self, item_id):
        """Move an item to the top, as happens when it is pasted."""
        item = self.get(item_id)
        if item is not None and self.items[0] is not item:
            self.items.remove(item)
            self.items.insert(0, item)
            self._changed()
        return item

    def remove(self, item_id):
        item = self.get(item_id)
        if item is not None:
            self.items.remove(item)
            self._delete_blobs(item)
            self._changed()
        return item

    def clear(self):
        """Remove everything except pinned items (the Windows "Clear all")."""
        removed = [item for item in self.items if not item.pinned]
        if removed:
            self.items = [item for item in self.items if item.pinned]
            for item in removed:
                self._delete_blobs(item)
            self._changed()
        return removed

    def set_pinned(self, item_id, pinned):
        item = self.get(item_id)
        if item is not None and item.pinned != pinned:
            item.pinned = pinned
            self._enforce_limit()
            self._changed()
        return item

    def set_max_items(self, count):
        self.max_items = count
        if self._enforce_limit():
            self._changed()

    def set_persist_all(self, persist_all):
        if self.persist_all != persist_all:
            self.persist_all = persist_all
            self._changed()

    def _enforce_limit(self):
        """Drop the oldest unpinned items beyond max_items. Pinned items don't count."""
        removed = False
        unpinned = [item for item in self.items if not item.pinned]
        for item in unpinned[self.max_items:]:
            self.items.remove(item)
            self._delete_blobs(item)
            removed = True
        return removed
