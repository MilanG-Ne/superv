"""Command line entry point.

    superv                 start the background service (same as `superv daemon`)
    superv toggle          open or close the panel (what Super+V runs)
    superv toggle --tab emoji
    superv show | hide | settings | clear | status | quit
    superv start           start the service in the background and return
    superv setup-shortcuts | remove-shortcuts
    superv autostart on|off

Client commands only import the socket helper, so they start fast. GTK is
imported only when the daemon itself starts.
"""

import os
import subprocess
import sys

from . import __version__, ipc

CLIENT_COMMANDS = ("toggle", "show", "hide", "settings", "clear", "status", "history", "quit", "ping")
STARTS_DAEMON = ("toggle", "show", "settings")
TABS = ("clipboard", "emoji", "kaomoji", "symbols")


def _usage():
    return __doc__.split("\n\n")[1].rstrip() + "\n"


def _spawn_daemon(initial=None):
    """Start the daemon detached from this process."""
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = package_parent + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    args = [sys.executable, "-m", "superv", "daemon"]
    if initial:
        args += ["--then", initial]
    subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "daemon"

    if command in ("-h", "--help", "help"):
        sys.stdout.write(_usage())
        return 0
    if command in ("-V", "--version", "version"):
        print("superv", __version__)
        return 0

    if command in CLIENT_COMMANDS:
        args = argv[1:]
        if "--tab" in args:
            position = args.index("--tab")
            if position + 1 >= len(args) or args[position + 1] not in TABS:
                print("superv: --tab must be one of: %s" % ", ".join(TABS), file=sys.stderr)
                return 2
        line = " ".join([command] + args)
        reply = ipc.send(line)
        if reply is None:
            if command in STARTS_DAEMON:
                _spawn_daemon(line)
                return 0
            if command == "status":
                print('{"running": false}')
                return 1
            print("superv: not running", file=sys.stderr)
            return 1
        if command in ("status", "history") or reply.startswith("error"):
            print(reply)
        return 1 if reply.startswith("error") else 0

    if command == "start":
        if ipc.send("ping") is None:
            _spawn_daemon()
        return 0

    if command == "daemon":
        initial = None
        if "--then" in argv:
            initial = " ".join(argv[argv.index("--then") + 1:]) or None
        from .app import run_daemon
        return run_daemon(initial)

    if command == "autostart" and argv[1:2] in (["on"], ["off"]):
        from . import desktop
        desktop.set_autostart(argv[1] == "on")
        return 0

    if command in ("setup-shortcuts", "remove-shortcuts"):
        from . import desktop
        if not desktop.is_xfce():
            if command == "setup-shortcuts":
                print(desktop.manual_shortcut_hint())
            else:
                print("Not Xfce: if you bound Super+V yourself, remove it in your keyboard settings.")
            return 1
        if command == "setup-shortcuts":
            registered, conflicts = desktop.setup_shortcuts()
            for key in registered:
                print("%s -> %s" % (desktop.pretty_shortcut(key), desktop.command(desktop.SHORTCUTS[key])))
            for key, value in conflicts:
                print("%s is already used by %r; not changed" % (desktop.pretty_shortcut(key), value))
            return 0 if registered else 1
        for key in desktop.remove_shortcuts():
            print("removed %s" % desktop.pretty_shortcut(key))
        return 0

    sys.stderr.write("superv: unknown command %r\n\n" % command + _usage())
    return 2


if __name__ == "__main__":
    sys.exit(main())
