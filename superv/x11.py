"""The few X11 calls GTK doesn't offer: which window is active, giving focus
back to it, and pressing Ctrl+V in it (XTest).

Uses ctypes against libX11/libXtst, which every X11 desktop already has, so
there is no need for xdotool or python-xlib. We open our own connection to
the X server. X errors on a connection GDK didn't open are ignored by GDK's
error handler, so a window vanishing under us can't crash the daemon.
"""

import ctypes
import ctypes.util
from ctypes import POINTER, Structure, Union, byref, c_char, c_char_p, c_int, c_long, c_ubyte, c_uint, c_ulong, c_void_p

Window = Atom = KeySym = c_ulong

CLIENT_MESSAGE = 33
SUBSTRUCTURE_NOTIFY_MASK = 1 << 19
SUBSTRUCTURE_REDIRECT_MASK = 1 << 20
SOURCE_PAGER = 2  # _NET_ACTIVE_WINDOW source indication window managers always honour

# Terminals paste with Ctrl+Shift+V. Matched against the WM_CLASS of the target window.
TERMINALS = {
    "alacritty", "cool-retro-term", "deepin-terminal", "foot", "gnome-terminal", "gnome-terminal-server",
    "guake", "hyper", "kitty", "konsole", "lxterminal", "mate-terminal", "org.wezfurlong.wezterm",
    "qterminal", "rxvt", "sakura", "st", "st-256color", "tabby", "terminator", "terminology", "tilda",
    "tilix", "urxvt", "uxterm", "wezterm", "xfce4-terminal", "xterm", "yakuake", "com.gexperts.tilix",
    "roxterm", "termite", "warp", "ghostty", "com.mitchellh.ghostty", "blackbox", "ptyxis",
}

MODIFIERS = ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Meta_L", "Meta_R",
             "Super_L", "Super_R", "Hyper_L", "Hyper_R", "ISO_Level3_Shift")


class _ClientMessage(Structure):
    _fields_ = [("type", c_int), ("serial", c_ulong), ("send_event", c_int), ("display", c_void_p),
                ("window", Window), ("message_type", Atom), ("format", c_int), ("data", c_long * 5)]


class _XEvent(Union):
    _fields_ = [("xclient", _ClientMessage), ("pad", c_long * 24)]


class _ClassHint(Structure):
    _fields_ = [("res_name", c_void_p), ("res_class", c_void_p)]


def _load(name):
    path = ctypes.util.find_library(name) or "lib%s.so.6" % name
    return ctypes.cdll.LoadLibrary(path)


class X11:
    """Raises OSError if libX11/libXtst are missing or the display can't be opened."""

    def __init__(self):
        self.xlib = xlib = _load("X11")
        self.xtst = xtst = _load("Xtst")
        xlib.XOpenDisplay.restype = c_void_p
        xlib.XOpenDisplay.argtypes = [c_char_p]
        xlib.XDefaultRootWindow.restype = Window
        xlib.XDefaultRootWindow.argtypes = [c_void_p]
        xlib.XInternAtom.restype = Atom
        xlib.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
        xlib.XGetWindowProperty.restype = c_int
        xlib.XGetWindowProperty.argtypes = [c_void_p, Window, Atom, c_long, c_long, c_int, Atom,
                                            POINTER(Atom), POINTER(c_int), POINTER(c_ulong),
                                            POINTER(c_ulong), POINTER(c_void_p)]
        xlib.XFree.argtypes = [c_void_p]
        xlib.XGetClassHint.restype = c_int
        xlib.XGetClassHint.argtypes = [c_void_p, Window, POINTER(_ClassHint)]
        xlib.XSendEvent.restype = c_int
        xlib.XSendEvent.argtypes = [c_void_p, Window, c_int, c_long, POINTER(_XEvent)]
        xlib.XFlush.argtypes = [c_void_p]
        xlib.XSync.argtypes = [c_void_p, c_int]
        xlib.XStringToKeysym.restype = KeySym
        xlib.XStringToKeysym.argtypes = [c_char_p]
        xlib.XKeysymToKeycode.restype = c_ubyte
        xlib.XKeysymToKeycode.argtypes = [c_void_p, KeySym]
        xlib.XQueryKeymap.argtypes = [c_void_p, c_char * 32]
        xtst.XTestFakeKeyEvent.restype = c_int
        xtst.XTestFakeKeyEvent.argtypes = [c_void_p, c_uint, c_int, c_ulong]

        self.display = xlib.XOpenDisplay(None)
        if not self.display:
            raise OSError("cannot open X display")
        self.root = xlib.XDefaultRootWindow(self.display)
        self._net_active_window = xlib.XInternAtom(self.display, b"_NET_ACTIVE_WINDOW", 0)

    # ---- windows ---------------------------------------------------------

    def active_window(self):
        """XID of the window the window manager considers active, or 0."""
        actual_type, actual_format = Atom(), c_int()
        count, remaining, data = c_ulong(), c_ulong(), c_void_p()
        status = self.xlib.XGetWindowProperty(
            self.display, self.root, self._net_active_window, 0, 1, 0, 0,
            byref(actual_type), byref(actual_format), byref(count), byref(remaining), byref(data))
        xid = 0
        if status == 0 and data.value:
            if count.value and actual_format.value == 32:
                xid = ctypes.cast(data, POINTER(c_ulong))[0]
            self.xlib.XFree(data)
        return xid

    def wm_class(self, xid):
        """(instance, class) of a window, lower-cased; empty strings if unknown."""
        if not xid:
            return "", ""
        hint = _ClassHint()
        if not self.xlib.XGetClassHint(self.display, xid, byref(hint)):
            return "", ""
        names = []
        for pointer in (hint.res_name, hint.res_class):
            names.append(ctypes.string_at(pointer).decode("utf-8", "replace").lower() if pointer else "")
            if pointer:
                self.xlib.XFree(pointer)
        return names[0], names[1]

    def is_terminal(self, xid):
        instance, klass = self.wm_class(xid)
        return instance in TERMINALS or klass in TERMINALS

    def activate(self, xid):
        """Ask the window manager to focus a window (like `xdotool windowactivate`)."""
        if not xid:
            return
        event = _XEvent()
        event.xclient.type = CLIENT_MESSAGE
        event.xclient.send_event = 1
        event.xclient.window = xid
        event.xclient.message_type = self._net_active_window
        event.xclient.format = 32
        event.xclient.data[0] = SOURCE_PAGER
        self.xlib.XSendEvent(self.display, self.root, 0,
                             SUBSTRUCTURE_REDIRECT_MASK | SUBSTRUCTURE_NOTIFY_MASK, byref(event))
        self.xlib.XFlush(self.display)

    # ---- keyboard --------------------------------------------------------

    def _keycode(self, name):
        keysym = self.xlib.XStringToKeysym(name.encode())
        return self.xlib.XKeysymToKeycode(self.display, keysym) if keysym else 0

    def _release_held_modifiers(self):
        """If the user still holds e.g. Super, Ctrl+V would arrive as Super+Ctrl+V."""
        keymap = (c_char * 32)()
        self.xlib.XQueryKeymap(self.display, keymap)
        pressed = bytes(keymap)
        for name in MODIFIERS:
            code = self._keycode(name)
            if code and pressed[code // 8] & (1 << (code % 8)):
                self.xtst.XTestFakeKeyEvent(self.display, code, 0, 0)

    def send_keys(self, names):
        codes = [self._keycode(name) for name in names]
        if not all(codes):
            return False
        self._release_held_modifiers()
        for code in codes:
            self.xtst.XTestFakeKeyEvent(self.display, code, 1, 0)
        for code in reversed(codes):
            self.xtst.XTestFakeKeyEvent(self.display, code, 0, 0)
        self.xlib.XSync(self.display, 0)
        return True

    def paste(self, xid):
        """Press the paste shortcut that suits the target window."""
        if self.is_terminal(xid):
            return self.send_keys(["Control_L", "Shift_L", "v"])
        return self.send_keys(["Control_L", "v"])
