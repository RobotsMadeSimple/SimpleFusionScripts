"""Joint analysis: what holds a part, floating parts, duplicates, connection map.

Pure Python (no adsk import) so it can be unit-tested outside Fusion.

A *record* is one joint-like thing read from the design (lib/collect.py):
    {"id", "kind", "name", "type", "parts": [occurrence paths], "health",
     "message", "suppressed", "locked", "context", "details": [...]}
kind: "joint" | "asBuilt" | "constraint" (assembly relationships) |
      "rigidGroup" | "motionLink"
health: "ok" | "warning" | "error"

A *part* is an occurrence: {"path", "name", "component", "grounded", "bodies"}.
Paths are Occurrence.fullPathName ("Frame:1+Roller:2").
"""

PATH_SEP = "+"

# Kinds that tie two parts together (a pair can be "held" twice by these).
PAIR_KINDS = ("joint", "asBuilt", "constraint")


def ancestors(path):
    """'A:1+B:1+C:1' -> ['A:1', 'A:1+B:1'] (outermost first)."""
    bits = path.split(PATH_SEP)
    return [PATH_SEP.join(bits[:i]) for i in range(1, len(bits))]


def is_self_or_ancestor(candidate, path):
    return path == candidate or path.startswith(candidate + PATH_SEP)


def for_part(records, path):
    """What holds `path`: records on the part itself, and records on its parent assemblies.

    For an assembly, its parts count as it: records between one of its parts and something
    outside are direct, and records between two of its own parts are "internal".

    Returns {"direct": [(other part path or "", [records])], "inherited": [(ancestor, [records])],
             "internal": [records]}.
    Direct records are grouped by the part on the other end (sorted by it);
    a record with several other parts appears under each. Records with no
    other part (e.g. joined to the root's origin) group under "".
    """
    direct = {}
    inherited = {}
    internal = []

    def within(p):
        return p == path or p.startswith(path + PATH_SEP)

    for rec in records:
        parts = rec["parts"]
        mine = [p for p in parts if within(p)]
        if mine:
            others = [p for p in parts if p and not within(p)]
            if others:
                for other in others:
                    direct.setdefault(other, []).append(rec)
            elif len(set(mine)) > 1:
                internal.append(rec)            # between two of the assembly's own parts
            else:
                direct.setdefault("", []).append(rec)
            continue
        for anc in ancestors(path):
            if anc in parts:
                inherited.setdefault(anc, []).append(rec)
                break
    return {
        "direct": sorted(direct.items(), key=lambda kv: (kv[0] == "", kv[0].lower())),
        "inherited": sorted(inherited.items(), key=lambda kv: len(kv[0])),
        "internal": internal,
    }


def floating(records, parts):
    """Parts with bodies that nothing holds: not grounded (nor a parent), and in no record
    (nor a parent). These can be dragged around freely, usually by mistake."""
    touched = set()
    for rec in records:
        if not rec.get("suppressed"):
            touched.update(rec["parts"])
    grounded = {p["path"] for p in parts if p.get("grounded")}
    out = []
    for part in parts:
        path = part["path"]
        if not part.get("bodies"):
            continue
        chain = ancestors(path) + [path]
        if any(c in grounded for c in chain) or any(c in touched for c in chain):
            continue
        out.append(path)
    return sorted(out, key=str.lower)


def duplicates(records):
    """Part pairs joined more than once by joints / as-built joints / relationships.

    Returns [{"pair": [a, b], "ids": [record ids]}], most records first.
    Suppressed records are ignored.
    """
    pairs = {}
    for rec in records:
        if rec["kind"] not in PAIR_KINDS or rec.get("suppressed"):
            continue
        parts = sorted(set(rec["parts"]))
        if len(parts) != 2:
            continue
        pairs.setdefault(tuple(parts), []).append(rec["id"])
    out = [{"pair": list(pair), "ids": ids} for pair, ids in pairs.items() if len(ids) > 1]
    out.sort(key=lambda d: (-len(d["ids"]), d["pair"]))
    return out


def problems(records):
    """Records with errors, then warnings (suppressed ones last within each)."""
    rank = {"error": 0, "warning": 1}
    bad = [r for r in records if r.get("health") in rank]
    bad.sort(key=lambda r: (rank[r["health"]], bool(r.get("suppressed")), r["name"].lower()))
    return bad


def graph(records, parts, roots=None):
    """Connection map: nodes are parts that appear in records (plus floating ones),
    edges are records between two parts.

    With `roots` ({path: assembly counted as one part}, layout.one_part_roots) a part inside
    such an assembly is drawn as that assembly: one node, and relationships inside it left out.

    Rigid groups link their parts in a chain (not all pairs) to keep the map
    readable; records with one part become a self-edge to a "Ground" node.
    Returns {"nodes": [{"id", "name", "grounded", "floating", "degree"}],
             "edges": [{"source", "target", "record", "kind", "health", "suppressed"}]}.
    """
    names = {p["path"]: p for p in parts}
    roots = roots or {}

    def node(path):
        return roots.get(path, path) if path else path

    float_set = set(node(p) for p in floating(records, parts))
    grounded = set(node(p["path"]) for p in parts if p.get("grounded"))
    edges = []
    used = set()
    for rec in records:
        ps = list(dict.fromkeys(node(p) for p in rec["parts"]))
        if len(rec["parts"]) > 1 and len(ps) == 1:
            continue                    # inside one assembly: not drawn
        if rec["kind"] == "rigidGroup" or len(ps) > 2:
            pairs = list(zip(ps, ps[1:]))
        elif len(ps) == 2:
            pairs = [(ps[0], ps[1])]
        elif len(ps) == 1:
            pairs = [(ps[0], "")]
        else:
            pairs = []
        for a, b in pairs:
            edges.append({"source": a, "target": b, "record": rec["id"], "kind": rec["kind"],
                          "health": rec.get("health", "ok"), "suppressed": bool(rec.get("suppressed"))})
            used.update([a, b])
    used.update(float_set)
    degree = {}
    for e in edges:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1
    nodes = []
    for path in sorted(used, key=str.lower):
        if path == "":
            nodes.append({"id": "", "name": "Ground / origin", "grounded": True, "floating": False,
                          "degree": degree.get("", 0)})
            continue
        part = names.get(path, {"name": path.split(PATH_SEP)[-1]})
        nodes.append({"id": path, "name": part.get("name", path), "grounded": path in grounded,
                      "floating": path in float_set, "degree": degree.get(path, 0)})
    return {"nodes": nodes, "edges": edges}


def summary(records):
    """Counts for the panel header."""
    out = {"total": len(records), "error": 0, "warning": 0, "suppressed": 0}
    for rec in records:
        if rec.get("health") in ("error", "warning"):
            out[rec["health"]] += 1
        if rec.get("suppressed"):
            out["suppressed"] += 1
    return out
