"""The user's own default settings, kept in their data folder (shared by every design)."""

import json
import os

from . import log, model, paths

FILE = "defaults.json"


def load():
    """Read the saved defaults into the model (or none). Returns True if there are any."""
    try:
        with open(paths.data_dir(FILE), encoding="utf-8") as f:
            values = json.load(f)
    except FileNotFoundError:
        values = {}
    except Exception:
        log.error("read my defaults")
        values = {}
    model.set_user_defaults(values if isinstance(values, dict) else {})
    return bool(values)


def save(settings):
    """Keep these manual settings as the user's defaults."""
    values = model.user_defaults_from(settings)
    os.makedirs(paths.data_dir(), exist_ok=True)
    with open(paths.data_dir(FILE), "w", encoding="utf-8") as f:
        json.dump(values, f, indent=1)
    model.set_user_defaults(values)


def clear():
    try:
        os.remove(paths.data_dir(FILE))
    except FileNotFoundError:
        pass
    model.set_user_defaults({})


def exists():
    return os.path.isfile(paths.data_dir(FILE))
