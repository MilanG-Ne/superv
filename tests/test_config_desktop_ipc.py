import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from superv import desktop, ipc, paths  # noqa: E402
from superv.config import DEFAULTS, Config  # noqa: E402


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_and_round_trip(self):
        config = Config(self.path)
        self.assertEqual(config["max_items"], DEFAULTS["max_items"])
        config.set("max_items", 50)
        self.assertEqual(Config(self.path)["max_items"], 50)

    def test_invalid_values_in_file_fall_back_to_defaults(self):
        with open(self.path, "w") as handle:
            json.dump({"max_items": "lots", "auto_paste": 1, "popup_position": "moon", "max_item_mb": 0}, handle)
        config = Config(self.path)
        self.assertEqual(config["max_items"], DEFAULTS["max_items"])
        self.assertEqual(config["auto_paste"], DEFAULTS["auto_paste"])
        self.assertEqual(config["popup_position"], "pointer")
        self.assertEqual(config["max_item_mb"], DEFAULTS["max_item_mb"])

    def test_set_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            Config(self.path).set("max_items", 0)

    def test_listeners_and_recent(self):
        config = Config(self.path)
        seen = []
        config.connect(lambda key, value: seen.append(key))
        config.remember_recent("recent_emoji", "🚀")
        config.remember_recent("recent_emoji", "😀")
        config.remember_recent("recent_emoji", "🚀")
        self.assertEqual(config["recent_emoji"], ["🚀", "😀"])
        self.assertEqual(seen, ["recent_emoji"] * 3)


class FakeXfconf:
    """Stands in for `xfconf-query -c xfce4-keyboard-shortcuts`."""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def __call__(self, args, **_kwargs):
        args = args[3:]  # drop "xfconf-query -c CHANNEL"
        prop = args[args.index("-p") + 1]
        result = mock.Mock(returncode=0, stdout="")
        if "-r" in args:
            self.values.pop(prop, None)
        elif "-s" in args:
            self.values[prop] = args[args.index("-s") + 1]
        elif prop in self.values:
            result.stdout = self.values[prop] + "\n"
        else:
            result.returncode = 1
        return result


class DesktopTest(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "XFCE",
                                                     "SUPERV_LAUNCHER": "/usr/bin/superv"}),
                        mock.patch("shutil.which", return_value="/usr/bin/xfconf-query"),
                        mock.patch("os.path.isfile", return_value=True),
                        # Never let a test reach the real xfconf.
                        mock.patch("subprocess.run", side_effect=AssertionError("unmocked xfconf call"))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_registers_both_shortcuts(self):
        fake = FakeXfconf()
        with mock.patch("subprocess.run", fake):
            registered, conflicts = desktop.setup_shortcuts()
        self.assertEqual(sorted(registered), ["<Super>period", "<Super>v"])
        self.assertEqual(conflicts, [])
        self.assertEqual(fake.values["/commands/custom/<Super>v"], "/usr/bin/superv toggle")
        self.assertEqual(fake.values["/commands/custom/<Super>period"], "/usr/bin/superv toggle --tab emoji")

    def test_never_overwrites_someone_elses_shortcut(self):
        fake = FakeXfconf({"/commands/custom/<Super>v": "vlc"})
        with mock.patch("subprocess.run", fake):
            registered, conflicts = desktop.setup_shortcuts()
            self.assertEqual(desktop.remove_shortcuts(), ["<Super>period"])
        self.assertEqual(registered, ["<Super>period"])
        self.assertEqual(conflicts, [("<Super>v", "vlc")])
        self.assertEqual(fake.values, {"/commands/custom/<Super>v": "vlc"})

    def test_pretty_names(self):
        self.assertEqual(desktop.pretty_shortcut("<Super>v"), "Super+V")
        self.assertEqual(desktop.pretty_shortcut("<Super>period"), "Super+.")


class AutostartTest(unittest.TestCase):
    def test_toggle_autostart(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}), \
                mock.patch.object(desktop, "SYSTEM_AUTOSTART", os.path.join(tmp, "none.desktop")):
            self.assertFalse(desktop.autostart_enabled())
            desktop.set_autostart(True)
            self.assertTrue(desktop.autostart_enabled())
            desktop.set_autostart(False)
            self.assertFalse(desktop.autostart_enabled())


class IpcTest(unittest.TestCase):
    def test_round_trip_with_a_long_runtime_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            deep = os.path.join(tmp, "x" * 60, "y" * 60)
            os.makedirs(deep, mode=0o700)
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": deep, "DISPLAY": ":7.0"}):
                self.assertTrue(paths.socket_path().endswith("superv-7.sock"))
                self.assertIsNone(ipc.send("ping"))
                server = ipc.listen()
                self.assertIsNotNone(server)
                self.assertIsNone(ipc.listen(), "a second daemon must not start")
                server.setblocking(True)

                def answer():
                    conn, _ = server.accept()
                    with conn:
                        conn.sendall(("echo:" + ipc.read_request(conn)).encode())

                thread = threading.Thread(target=answer)
                thread.start()
                self.assertEqual(ipc.send("status"), "echo:status")
                thread.join()
                server.close()
                mode = os.stat(paths.socket_path()).st_mode & 0o777
                self.assertEqual(mode, 0o600)

    def test_display_ids(self):
        for display, expected in ((":0", "0"), (":0.0", "0"), ("localhost:10.0", "10"), ("", "none")):
            with mock.patch.dict(os.environ, {"DISPLAY": display}):
                self.assertEqual(paths.display_id(), expected)


class SocketCleanupTest(unittest.TestCase):
    def test_stale_socket_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp, "DISPLAY": ":5"}):
            os.makedirs(os.path.dirname(paths.socket_path()), exist_ok=True)
            stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stale.bind(paths.socket_path())
            stale.close()  # file exists, nobody listening
            server = ipc.listen()
            self.assertIsNotNone(server)
            server.close()


if __name__ == "__main__":
    unittest.main()
