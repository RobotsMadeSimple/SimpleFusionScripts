"""Where BuildBook keeps its own files, and opening files the way the OS does.

BuildBook's working files (log, hardware-label cache, step thumbnails) live in a per-user
folder, not in the add-in's folder, which an installer (e.g. the Autodesk App Store) may
replace on update:

    Windows  %APPDATA%\\RobotsMadeSimple\\BuildBook
    macOS    ~/Library/Application Support/RobotsMadeSimple/BuildBook

Nothing here imports adsk, so it's usable everywhere (and in tests).
"""

import os
import shutil
import subprocess
import sys

VENDOR = "RobotsMadeSimple"
APP = "BuildBook"
ADDIN_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def data_dir(*parts):
    """The per-user data folder (or a path inside it). Not created here."""
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, VENDOR, APP, *parts)


def migrate_old_files():
    """Earlier versions kept thumbnails and the label cache inside the add-in folder: copy
    them over once, so nothing is lost. Returns how many files were copied."""
    copied = 0
    for name in ("thumbs", "cache"):
        old, new = os.path.join(ADDIN_DIR, name), data_dir(name)
        if not os.path.isdir(old):
            continue
        try:
            os.makedirs(new, exist_ok=True)
            for entry in os.listdir(old):
                src, dst = os.path.join(old, entry), os.path.join(new, entry)
                if os.path.isfile(src) and not os.path.exists(dst):
                    shutil.copy2(src, dst)
                    copied += 1
        except OSError:
            pass
    return copied


def open_path(path):
    """Open a file or folder with the system's default app (Explorer / Finder / viewer)."""
    if os.name == "nt":
        os.startfile(path)          # noqa: (Windows only)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
