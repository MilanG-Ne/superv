"""Talking to the running daemon over a Unix socket.

The client half is deliberately GTK-free so that `superv toggle`, which the
Super+V shortcut runs, starts in a few milliseconds.
"""

import fcntl
import os
import socket
import time

from . import paths

MAX_SOCKET_PATH = 100  # sun_path is 108 bytes on Linux


class _SocketPath:
    """Unix socket paths are limited to 108 bytes. For longer ones, reach the
    directory through /proc/self/fd, which keeps the path short."""

    def __init__(self, path):
        self.path, self._fd = path, None

    def __enter__(self):
        if len(self.path.encode()) < MAX_SOCKET_PATH:
            return self.path
        self._fd = os.open(os.path.dirname(self.path), os.O_RDONLY | os.O_DIRECTORY)
        return "/proc/self/fd/%d/%s" % (self._fd, os.path.basename(self.path))

    def __exit__(self, *_exc):
        if self._fd is not None:
            os.close(self._fd)


def send(command, timeout=2.0):
    """Send one command line to the daemon. Returns its reply, or None if no
    daemon is listening."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        with _SocketPath(paths.socket_path()) as path:
            sock.connect(path)
        sock.sendall(command.encode("utf-8") + b"\n")
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", "replace")
    except (FileNotFoundError, NotADirectoryError, ConnectionRefusedError):
        return None
    except socket.timeout:
        return None
    finally:
        sock.close()


_lock = None  # held for the daemon's lifetime


def listen():
    """Create the daemon's listening socket. Returns None if another daemon
    is running or starting. A lock file, not the socket, decides who wins, so
    two daemons started at the same moment can't both think they're first."""
    global _lock
    path = paths.socket_path()
    paths.ensure_dir(os.path.dirname(path))
    lock = os.open(path + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock)
        return None
    if _lock is not None:
        os.close(_lock)
    _lock = lock
    try:
        os.unlink(path)  # stale socket left behind by a crash
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old_umask = os.umask(0o177)
    try:
        with _SocketPath(path) as short_path:
            server.bind(short_path)
    finally:
        os.umask(old_umask)
    server.listen(8)
    server.setblocking(False)
    return server


def send_when_ready(command, wait=5.0):
    """Send a command to a daemon that may still be starting up."""
    deadline = time.monotonic() + wait
    while True:
        reply = send(command)
        if reply is not None or time.monotonic() > deadline:
            return reply
        time.sleep(0.1)


def read_request(conn):
    conn.settimeout(1.0)
    data = b""
    while b"\n" not in data and len(data) < 4096:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    return data.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
