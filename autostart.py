"""Runs Server Manager in the background, with no window, and starts it again when it stops.

install_autostart.ps1 makes a task that runs this with pythonw.exe at sign-in (and again within a minute if it isn't
running). Server Manager's output goes to server.log (the two before it: server.log.1, server.log.2). While something
already answers on Server Manager's port (a copy started by start.bat, say), this waits instead of starting another.
"""
import ctypes
import logging
import os
import re
import socket
import subprocess
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).parent
PYTHON = BASE_DIR / "venv" / "Scripts" / "python.exe"
LOG_FILE = BASE_DIR / "server.log"
MUTEX = "Local\\ServerManagerAutostart"
CHECK_EVERY = 10.0  # while another copy answers on the port
RESTART_PAUSE = 5.0
QUICK_EXIT, QUICK_EXIT_PAUSE = 60.0, 30.0  # it stopped within a minute of starting: wait longer before the next try

log = logging.getLogger("server_manager")


def port() -> int:
    # main.py's PORT, read rather than imported: importing main would load FastAPI into this small process too
    return int(re.search(r"^PORT = (\d+)", (BASE_DIR / "main.py").read_text(encoding="utf-8"), re.M).group(1))


def answering(number: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", number), timeout=1):
            return True
    except OSError:
        return False


def already_running() -> bool:
    """Another copy of this runner holds the mutex (it stays held while this process lives)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    already_running.handle = kernel32.CreateMutexW(None, False, MUTEX)
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def run_once() -> None:
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    # CREATE_NO_WINDOW alone: venv's python.exe is a launcher that starts the real one, which then shares its hidden
    # console (with DETACHED_PROCESS the real one would open a visible window)
    process = subprocess.Popen([str(PYTHON), "main.py"], cwd=BASE_DIR, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    log.info("Started Server Manager (process %d)", process.pid)
    for line in process.stdout:
        log.info("%s", line.decode("utf-8", "replace").rstrip())
    log.warning("Server Manager stopped (exit code %s)", process.wait())


def main() -> None:
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    if already_running():
        return
    while True:
        try:
            if answering(port()):
                time.sleep(CHECK_EVERY)
                continue
            started = time.monotonic()
            run_once()
            time.sleep(QUICK_EXIT_PAUSE if time.monotonic() - started < QUICK_EXIT else RESTART_PAUSE)
        except Exception:
            log.exception("Couldn't run Server Manager")
            time.sleep(QUICK_EXIT_PAUSE)


if __name__ == "__main__":
    main()
