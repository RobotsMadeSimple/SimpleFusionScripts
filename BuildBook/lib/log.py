"""Plain-text log at BuildBook/logs/buildbook.log (tracebacks + timings)."""

import datetime
import os
import time
import traceback
from contextlib import contextmanager

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "logs")
_LOG_PATH = os.path.join(_LOG_DIR, "buildbook.log")
_MAX_BYTES = 2 * 1024 * 1024


def _write(line):
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        if os.path.exists(_LOG_PATH) and os.path.getsize(_LOG_PATH) > _MAX_BYTES:
            os.replace(_LOG_PATH, _LOG_PATH + ".1")
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write("{} {}\n".format(stamp, line))
    except Exception:
        pass


def info(message):
    _write("INFO  " + message)


def error(message):
    _write("ERROR " + message + "\n" + traceback.format_exc())


@contextmanager
def timed(label):
    start = time.perf_counter()
    try:
        yield
    finally:
        _write("TIME  {} {:.0f} ms".format(label, (time.perf_counter() - start) * 1000))
