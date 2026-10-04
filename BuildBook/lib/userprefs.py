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


BRANDING_FILE = "branding.json"
BRANDING_KEYS = ("company", "author", "logo")


_branding_cache = None


def branding():
    """The user's company / author / logo, used by every build book that has no override."""
    global _branding_cache
    if _branding_cache is not None:
        return dict(_branding_cache)        # (read once: the panel asks on every refresh)
    try:
        with open(paths.data_dir(BRANDING_FILE), encoding="utf-8") as f:
            values = json.load(f)
    except FileNotFoundError:
        values = {}
    except Exception:
        log.error("read my company details")
        values = {}
    _branding_cache = {k: (values.get(k) or "") if isinstance(values, dict) else "" for k in BRANDING_KEYS}
    return dict(_branding_cache)


def has_branding():
    """True if the user has saved company details for future books."""
    return any(branding().values())


def save_branding(values):
    """Keep company / author / logo for every build book without its own."""
    global _branding_cache
    values = {k: (values.get(k) or "") for k in BRANDING_KEYS}
    _branding_cache = dict(values)
    os.makedirs(paths.data_dir(), exist_ok=True)
    with open(paths.data_dir(BRANDING_FILE), "w", encoding="utf-8") as f:
        json.dump(values, f)


def own_fields(settings):
    """The company / author / logo fields this book sets itself."""
    if settings.get("brandingOwn"):         # (older: all of them)
        return set(BRANDING_KEYS)
    return set(settings.get("brandOwnFields") or []) & set(BRANDING_KEYS)


def locked_fields(settings):
    """Fields showing the saved default (there is one, and the book doesn't set its own)."""
    saved, own = branding(), own_fields(settings)
    return {k for k in BRANDING_KEYS if saved[k] and k not in own}


def effective_branding(settings):
    """What a book's PDF uses, field by field: the saved default, unless the book sets its own
    (or there is no default for that field)."""
    saved, locked = branding(), locked_fields(settings)
    return {k: saved[k] if k in locked else (settings.get(k) or "") for k in BRANDING_KEYS}


def exists():
    return os.path.isfile(paths.data_dir(FILE))
