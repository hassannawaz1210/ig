"""Server log file setup. One-off CLI commands keep Python's stderr default."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from . import config

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def log_file():
    return config.CACHE / "server.log"


def setup(console: bool = False) -> None:
    """Log to ~/.cache/ig/server.log (1 MB × 3 files), and to stderr if asked.

    IG_LOG_LEVEL=DEBUG shows every Instagram request."""
    root = logging.getLogger("ig")
    if getattr(root, "_ig_configured", False):
        return
    level = getattr(logging, os.environ.get("IG_LOG_LEVEL", "INFO").upper(), logging.INFO)
    root.setLevel(level)
    formatter = logging.Formatter(FORMAT)
    try:
        log_file().parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(formatter)
        root.addHandler(handler)
    except OSError as error:
        console = True
        print(f"ig: could not open the log file {log_file()}: {error}", file=sys.stderr)
    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)
    root._ig_configured = True


def tail(lines: int = 40) -> list[str]:
    try:
        with log_file().open(encoding="utf-8", errors="replace") as file:
            return file.read().splitlines()[-lines:]
    except FileNotFoundError:
        return []
