"""The background daemon: owns the history, watches the clipboard, answers
`superv toggle` and friends over a Unix socket, and shows the popup."""

import json
import os
import signal
import sys

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, __version__, desktop, ipc, paths  # noqa: E402
from .config import Config  # noqa: E402
from .monitor import ClipboardMonitor  # noqa: E402
from .store import KIND_TEXT, Store  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PASTE_DELAY_MS = 80  # let the popup disappear and focus go back first
FOCUS_DELAY_MS = 60
RESTORE_AFTER_INSERT_MS = 250
RESTORE_FALLBACK_MS = 2500


class App:
    def __init__(self):
        GLib.set_prgname(APP_ID)
        GLib.set_application_name(APP_NAME)
        Gdk.set_program_class("SuperV")
        self.config = Config()
        self.store = Store(max_items=self.config["max_items"],
                           persist_all=self.config["keep_history_after_restart"])
        self.store.load()
        self.monitor = ClipboardMonitor(self.store, self.config, self._selection())
        self.popup = None
        self.settings = None
        self.tray = None
        self.target_window = 0
        self._insert_token = 0
        try:
            from .x11 import X11
            self.x11 = X11()
        except OSError as error:
            print("superv: automatic paste is unavailable: %s" % error, file=sys.stderr)
            self.x11 = None

        self._load_style()
        self.store.connect(self._on_store_changed)
        self.config.connect(self._on_config_changed)
        self._update_tray()

    @staticmethod
    def _selection():
        # Tests run against a private selection so they never touch the real clipboard.
        name = os.environ.get("SUPERV_SELECTION")
        return Gdk.Atom.intern(name, False) if name else Gdk.SELECTION_CLIPBOARD

    def _load_style(self):
        Gtk.IconTheme.get_default().append_search_path(os.path.join(HERE, "icons"))
        provider = Gtk.CssProvider()
        provider.load_from_path(os.path.join(HERE, "style.css"))
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    # ---- popup -----------------------------------------------------------------

    def _ensure_popup(self):
        if self.popup is None:
            from .popup import Popup
            self.popup = Popup(self)
        return self.popup

    def _own_window(self, xid):
        windows = [self.popup, self.settings]
        return any(w is not None and w.get_realized() and w.get_window().get_xid() == xid for w in windows)

    def show_popup(self, tab=None):
        popup = self._ensure_popup()
        if not popup.get_visible() and self.x11 is not None:
            active = self.x11.active_window()
            if not self._own_window(active):
                self.target_window = active
        popup.open(tab)

    def toggle_popup(self, tab=None):
        popup = self._ensure_popup()
        if popup.get_visible() and (tab is None or tab == popup.current_tab):
            popup.hide()
        else:
            self.show_popup(tab)

    def hide_popup(self):
        if self.popup is not None:
            self.popup.hide()

    # ---- actions used by the popup, tray and settings ------------------------------

    def use_item(self, item_id, plain=False, paste=True):
        """Put a history item on the clipboard and (normally) paste it."""
        item = self.store.get(item_id)
        if item is None:
            return
        self.hide_popup()
        self.store.touch(item_id)
        self.monitor.use(item, plain)
        if paste and self.config["auto_paste"]:
            self._paste_into_target()

    def insert_text(self, text):
        """Type an emoji, kaomoji or symbol into the app you were using."""
        self.hide_popup()
        if not (self.config["auto_paste"] and self._can_paste()):
            # Nowhere to paste: just copy it, as a normal history item.
            if self.config["history_enabled"]:
                self.monitor.serve(self.store.add(KIND_TEXT, text))
            else:
                self.monitor.serve_text(text)
            return
        self._insert_token += 1
        token = self._insert_token

        def restore():
            self._restore_after_insert(token, text)
            return False

        def pasted():
            GLib.timeout_add(RESTORE_AFTER_INSERT_MS, restore)
            return False

        self.monitor.serve_text(text, on_fetched=pasted)
        self._paste_into_target()
        GLib.timeout_add(RESTORE_FALLBACK_MS, restore)

    def _restore_after_insert(self, token, text):
        """The emoji is pasted; put back what was on the clipboard before."""
        if token == self._insert_token and self.monitor.serving_text(text):
            newest = self.store.newest() if self.config["history_enabled"] else None
            if newest is not None:
                self.monitor.serve(newest)
            else:
                self.monitor.release()

    def _can_paste(self):
        return self.x11 is not None and bool(self.target_window)

    def _paste_into_target(self):
        if not self._can_paste():
            return
        target = self.target_window

        def focus_target():
            if self.x11.active_window() != target:
                self.x11.activate(target)
            GLib.timeout_add(FOCUS_DELAY_MS, press_paste)
            return False

        def press_paste():
            self.x11.paste(target)
            return False

        GLib.timeout_add(PASTE_DELAY_MS, focus_target)

    def set_pinned(self, item_id, pinned):
        self.store.set_pinned(item_id, pinned)
        if self.popup is not None:
            self.popup.pages["clipboard"].restore_focus_after_change()

    def delete_item(self, item_id):
        self.monitor.forget(item_id)
        self.store.remove(item_id)
        if self.popup is not None:
            self.popup.pages["clipboard"].restore_focus_after_change()

    def clear_history(self, clear_clipboard=None):
        """Remove unpinned items. clear_clipboard: True also empties the
        clipboard (Settings > Clear), None empties it only if what's on it was
        just removed (the popup's "Clear all"), False leaves it alone."""
        current = self.store.get(self.monitor.current_item_id or "")
        removed = self.store.clear()
        if clear_clipboard is False or (current is not None and current.pinned):
            return
        if clear_clipboard or (current is not None and current in removed):
            self.monitor.clear_current()

    def open_settings(self):
        self.hide_popup()
        if self.settings is None:
            from .settings import SettingsWindow
            self.settings = SettingsWindow(self)
        self.settings.present_now()

    # ---- reacting to changes ---------------------------------------------------

    def _on_store_changed(self):
        if self.popup is not None:
            self.popup.pages["clipboard"].mark_dirty()

    def _on_config_changed(self, key, value):
        if key == "max_items":
            self.store.set_max_items(value)
        elif key == "keep_history_after_restart":
            self.store.set_persist_all(value)
        elif key == "history_enabled" and not value:
            # Like Windows: switching history off clears it (pinned items stay).
            self.clear_history(clear_clipboard=False)
        elif key == "show_tray_icon":
            self._update_tray()
        if key in ("history_enabled", "pinned_first", "auto_paste") and self.popup is not None:
            self.popup.pages["clipboard"].mark_dirty()

    def _update_tray(self):
        if self.config["show_tray_icon"] and self.tray is None:
            from .tray import Tray
            self.tray = Tray(self)
        if self.tray is not None:
            self.tray.set_visible(self.config["show_tray_icon"])

    # ---- commands from `superv <command>` ---------------------------------------------

    def handle(self, line):
        words = line.split()
        if not words:
            return "error: empty command"
        command, args = words[0], words[1:]
        tab = args[args.index("--tab") + 1] if "--tab" in args[:-1] else None
        if command == "ping":
            return "pong"
        if command == "toggle":
            self.toggle_popup(tab)
        elif command == "show":
            self.show_popup(tab)
        elif command == "hide":
            self.hide_popup()
        elif command == "settings":
            self.open_settings()
        elif command == "clear":
            self.clear_history()
        elif command == "status":
            return json.dumps({
                "version": __version__,
                "pid": os.getpid(),
                "history_enabled": self.config["history_enabled"],
                "items": len(self.store.items),
                "pinned": sum(1 for item in self.store.items if item.pinned),
                "popup_visible": bool(self.popup and self.popup.get_visible()),
                "current_tab": self.popup.current_tab if self.popup else None,
                "auto_paste": self._can_paste() if self.config["auto_paste"] else False,
            })
        elif command == "history":
            return json.dumps([{"kind": item.kind, "text": item.text[:200], "pinned": item.pinned,
                                "formats": item.formats, "width": item.width, "height": item.height}
                               for item in self.store.items], ensure_ascii=False)
        elif command == "quit":
            GLib.idle_add(self.quit)
        else:
            return "error: unknown command %s" % command
        return "ok"

    def _on_ipc(self, _fd, _condition, server):
        try:
            conn, _ = server.accept()
        except BlockingIOError:
            return True
        with conn:
            try:
                reply = self.handle(ipc.read_request(conn))
                conn.sendall(reply.encode("utf-8"))
            except OSError:
                pass
        return True

    # ---- lifecycle -------------------------------------------------------------------

    def offer_shortcuts(self):
        """First run on Xfce: register Super+V and Super+. once. After that the
        user decides in Settings."""
        if not self.config["shortcuts_offered"] and desktop.is_xfce():
            registered, conflicts = desktop.setup_shortcuts()
            for key, value in conflicts:
                print("superv: %s is already bound to %r, leaving it alone" % (key, value), file=sys.stderr)
            self.config.set("shortcuts_offered", True)

    def run(self, server, initial=None):
        self.server = server
        GLib.io_add_watch(server.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, self._on_ipc, server)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, self.quit)
        if os.environ.get("SUPERV_NO_SHORTCUTS") != "1":
            self.offer_shortcuts()
        if initial:
            GLib.idle_add(lambda: self.handle(initial) and False)
        Gtk.main()

    def quit(self, *_args):
        try:
            self.store.save()
        finally:
            try:
                os.unlink(paths.socket_path())
            except OSError:
                pass
            Gtk.main_quit()
        return False


def run_daemon(initial=None):
    server = ipc.listen()
    if server is None:
        print("superv: already running", file=sys.stderr)
        if initial:
            ipc.send_when_ready(initial)
        return 0
    problem = None
    if not Gtk.init_check(sys.argv)[0]:
        problem = "cannot open the display (SuperV needs an X11 session)"
    elif not isinstance(Gdk.Display.get_default(), GdkX11.X11Display):
        problem = "SuperV needs an X11 session; Wayland isn't supported yet"
    if problem:
        print("superv: " + problem, file=sys.stderr)
        server.close()
        os.unlink(paths.socket_path())
        return 1
    App().run(server, initial)
    return 0
