"""A record on disk of the parts BuildBook has switched off, so a crash can't leave them hidden.

The step view and the Pick dialog hide parts with their light bulbs and switch them back on
when they finish. If Fusion crashes in between, nothing switches them back on and the design
is saved / recovered with them hidden. Each owner ("scene", "pick") writes what it has hidden
here; `recover` (at start-up and on a document switch) turns on whatever is still listed.
"""

import json
import os

import adsk.core
import adsk.fusion

from . import log, paths, refs

FILE = "hidden-parts.json"


def _path():
    return paths.data_dir(FILE)


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data):
    try:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        log.error("write the hidden-parts record")


def _doc_key(design):
    doc = design.parentDocument
    try:
        if doc.dataFile is not None:
            return doc.dataFile.id
    except Exception:
        pass
    return doc.name


def record(design, owner, occ_paths, bodies):
    """Note what `owner` has hidden now: occurrence paths and (occurrence path, body name)
    pairs. Empty lists clear its entry."""
    try:
        key = _doc_key(design)
    except Exception:
        return
    data = _load()
    entry = data.get(key, {})
    occ_paths = sorted(set(occ_paths))
    bodies = sorted(set(tuple(b) for b in bodies))
    old = entry.get(owner)
    if occ_paths or bodies:
        new = {"occs": occ_paths, "bodies": [list(b) for b in bodies]}
        if old == new:
            return
        entry[owner] = new
    else:
        if old is None:
            return
        entry.pop(owner, None)
    if entry:
        data[key] = entry
    else:
        data.pop(key, None)
    _write(data)


def body_entry(body):
    """(occurrence path, body name) of a body proxy, or None for a root body."""
    occ = body.assemblyContext
    return (occ.fullPathName, body.name) if occ is not None else None


def recover(design):
    """Turn back on every part a previous session (one that crashed) left hidden."""
    try:
        key = _doc_key(design)
    except Exception:
        return 0
    data = _load()
    entry = data.pop(key, None)
    if not entry:
        return 0
    index = refs.path_index(design)
    turned = 0
    for owner in entry.values():
        for path in owner.get("occs", []):
            occ = index.get(path)
            occ = getattr(occ, "occ", occ)          # (a split part's entry: its occurrence)
            try:
                if occ is not None and not occ.isLightBulbOn:
                    occ.isLightBulbOn = True
                    turned += 1
            except Exception:
                log.error("recover " + path)
        for path, name in owner.get("bodies", []):
            occ = index.get(path)
            occ = getattr(occ, "occ", occ)
            try:
                body = occ.bRepBodies.itemByName(name) if occ is not None else None
                if body is not None and not body.isLightBulbOn:
                    body.isLightBulbOn = True
                    turned += 1
            except Exception:
                log.error("recover {} {}".format(path, name))
    _write(data)
    log.info("recovered {} part(s) left hidden by an earlier session".format(turned))
    return turned
