#!/usr/bin/env python3
"""End-to-end check against a real X server: start the daemon, copy things
with xclip, and check they land in the history and come back out.

    xvfb-run -a python3 tests/smoke_test.py

Uses throwaway XDG directories, so it never touches your real history.
Needs: an X display, xclip, python3-gi, gir1.2-gtk-3.0.
"""

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LAUNCHER = os.path.join(ROOT, "bin", "superv")


def tiny_png(width=3, height=2):
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def main():
    if not os.environ.get("DISPLAY") or not shutil.which("xclip"):
        print("smoke test needs DISPLAY and xclip")
        return 1
    tmp = tempfile.mkdtemp(prefix="superv-smoke-")
    env = dict(os.environ)
    for name in ("CONFIG", "DATA", "CACHE"):
        env["XDG_%s_HOME" % name] = os.path.join(tmp, name.lower())
    env["XDG_RUNTIME_DIR"] = os.path.join(tmp, "run")
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
    env["XDG_CURRENT_DESKTOP"] = "SMOKE"
    env["SUPERV_NO_SHORTCUTS"] = "1"

    def superv(*args):
        return subprocess.run([sys.executable, LAUNCHER] + list(args), env=env,
                              capture_output=True, text=True, timeout=10)

    def copy(data, target=None):
        args = ["xclip", "-selection", "clipboard", "-i"] + (["-t", target] if target else [])
        subprocess.run(args, input=data, env=env, check=True, timeout=5)
        time.sleep(0.5)

    def paste(target=None):
        args = ["xclip", "-selection", "clipboard", "-o"] + (["-t", target] if target else [])
        return subprocess.run(args, env=env, capture_output=True, timeout=5).stdout

    def history():
        return json.loads(superv("history").stdout)

    daemon = subprocess.Popen([sys.executable, LAUNCHER, "daemon"], env=env)
    failures = []

    def check(condition, message):
        print(("ok    " if condition else "FAIL  ") + message)
        if not condition:
            failures.append(message)

    try:
        for _ in range(50):
            if superv("status").returncode == 0:
                break
            time.sleep(0.1)
        check(superv("status").returncode == 0, "daemon answers `superv status`")

        copy(b"first copy")
        copy(b"second copy")
        copy(tiny_png(), "image/png")
        copy(b"first copy")
        items = history()
        check([i["kind"] for i in items] == ["text", "image", "text"], "text and image captured, duplicate moved to top")
        check(items[0]["text"] == "first copy" and items[1]["width"] == 3, "item details recorded")

        superv("toggle")
        time.sleep(0.5)
        status = json.loads(superv("status").stdout)
        check(status["popup_visible"] and status["current_tab"] == "clipboard", "Super+V panel opens")
        superv("toggle", "--tab", "emoji")
        time.sleep(0.3)
        check(json.loads(superv("status").stdout)["current_tab"] == "emoji", "emoji tab opens")
        superv("hide")

        superv("clear")
        check(history() == [], "Clear all empties unpinned history")
        check(paste() == b"", "clipboard emptied after clearing what was on it")
    finally:
        superv("quit")
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()
            failures.append("daemon did not quit")
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%s" % ("all good" if not failures else "%d failure(s)" % len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
