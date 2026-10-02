"""Select objects the API won't select (relationships) through Fusion's text commands.

`ui.activeSelections.add` rejects assembly relationships ("invalid argument
entity"), but the text command `Selections.Set <path>` selects anything by its
selection path, e.g. "57:3:13:311" for a top-level relationship. The last
number relates to the id at the end of the entity token (token + 82 in the
first design measured); the rest is the path of the component that holds it.

What is learned, per design (kept on disk):
- exact paths: token id -> selection path, from every relationship the user
  selects or right-clicks in Fusion (and every successful selection here);
- "prefix": the top-level path ("57:3:13"), from a relationship, else from a
  top-level joint / rigid group the API can select;
- "offset": selection id minus token id for relationships (a search around
  the last known value finds it when it's new).
Every selection is verified (type + name), so a wrong guess never edits the
wrong thing.
"""

import base64
import gzip
import io
import json
import os
import re

from . import log

_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "cache", "selection_paths.json")
_PATH = re.compile(r"\d+(?::\d+)+")
DEFAULT_PREFIX = "57:3:13"   # top level, measured on the first design
DEFAULT_OFFSET = 82          # measured on the first design
SEARCH = 150                 # try offsets this far either side of the known one
_data = None


def token_id(token):
    """The internal id at the end of a decoded entity token, or None."""
    try:
        raw = base64.b64decode(token)
        start = raw.find(b"\x1f\x8b")
        data = gzip.GzipFile(fileobj=io.BytesIO(raw[start:])).read()
        return int(data.split()[-1])
    except Exception:
        return None


def selection_paths(app):
    """Paths of the current Fusion selection, from the text command Selections.List."""
    try:
        return _PATH.findall(app.executeTextCommand("Selections.List"))
    except Exception as error:
        log.info("Selections.List failed: {}".format(error))
        return []


def _store():
    global _data
    if _data is None:
        try:
            with open(_FILE, encoding="utf-8") as handle:
                _data = json.load(handle)
        except Exception:
            _data = {}
    return _data


def _doc(doc_key):
    entry = _store().setdefault(doc_key, {})
    entry.setdefault("paths", {})
    return entry


def _save():
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with open(_FILE, "w", encoding="utf-8") as handle:
            json.dump(_store(), handle)
    except Exception:
        log.error("save selection paths")


def learn_relationship(doc_key, path, token):
    """A relationship the user selected in Fusion: remember its path, prefix and offset."""
    ident = token_id(token)
    if ident is None or not path:
        return False
    prefix, _, last = path.rpartition(":")
    entry = _doc(doc_key)
    entry["paths"][str(ident)] = path
    if prefix.count(":") == 2:                      # top level ("57:3:13")
        entry["prefix"] = prefix
        entry["offset"] = int(last) - ident
    _save()
    log.info("learned relationship path {} (token id {})".format(path, ident))
    return True


def learn_prefix(doc_key, path):
    """Top-level prefix from a top-level object the API could select."""
    prefix = path.rpartition(":")[0]
    if prefix.count(":") == 2 and _doc(doc_key).get("prefix") != prefix:
        _doc(doc_key)["prefix"] = prefix
        _save()
        log.info("selection path prefix {}".format(prefix))


def _try(app, ui, path, entity_type, name):
    try:
        app.executeTextCommand("Selections.Set " + path)
        sels = ui.activeSelections
        if sels.count == 1:
            got = sels.item(0).entity
            return got.objectType == entity_type and getattr(got, "name", None) == name
    except Exception:
        pass
    return False


def select(app, ui, doc_key, token, entity_type, name, top_level):
    """Select a relationship by its token through Selections.Set. True only if verified."""
    ident = token_id(token)
    if ident is None:
        return False
    entry = _doc(doc_key)
    exact = entry["paths"].get(str(ident))
    if exact and _try(app, ui, exact, entity_type, name):
        log.info("Selections.Set {} (remembered) selected {}".format(exact, name))
        return True
    prefix = entry.get("prefix", DEFAULT_PREFIX)
    if not top_level:
        log.info("no selection path known for {} yet".format(name))
        return False
    known = entry.get("offset", DEFAULT_OFFSET)
    offsets = [known] + [known + d * s for d in range(1, SEARCH + 1) for s in (1, -1)]
    for offset in offsets:
        path = "{}:{}".format(prefix, ident + offset)
        if _try(app, ui, path, entity_type, name):
            entry["paths"][str(ident)] = path
            entry["offset"] = offset
            _save()
            log.info("Selections.Set {} selected {} (offset {})".format(path, name, offset))
            return True
    try:
        ui.activeSelections.clear()
    except Exception:
        pass
    log.info("couldn't find a selection path for {} (prefix {}, offsets {}..{})".format(
        name, prefix, known - SEARCH, known + SEARCH))
    return False
