"""Part pictures for the Tree and BOM rows: a coarse mesh of each component, drawn by the panel.

Nothing is rendered in Fusion's viewport: a component's bodies (and its sub-parts') are meshed
at low quality, placed in the component's own space, and sent to the panel, which draws a small
shaded isometric picture (palette/thumbs.js) and keeps it in the browser cache under the
component id. The picture's key is the component plus its bodies' revision ids, so the panel
asks for a new mesh only after the part changes, and every copy of a part shares one picture.
(Same approach as BuildBook's lib/partthumbs.py.)
"""

import hashlib

import adsk.fusion

from . import log

MAX_TRIANGLES = 4000          # per part: plenty for a 26 px picture
MAX_BODIES = 200
_meshes = {}                  # (component id, body name, revision) -> (coords, indices) this session


def _bodies(occ):
    """[(native body, Matrix3D placing it in occ's component space)] of occ and its sub-parts."""
    out = []
    inv = None
    try:
        inv = occ.transform2.copy()
        if not inv.invert():
            inv = None
    except Exception:
        log.error("part picture: placement of " + _safe_name(occ))
    stack = [occ]
    while stack and len(out) < MAX_BODIES:
        o = stack.pop()
        try:
            m = o.transform2
            if inv is not None:
                m = m.copy()
                m.transformBy(inv)
            for body in o.bRepBodies:
                if body.isSolid:
                    out.append((body.nativeObject or body, m))
            stack.extend(o.childOccurrences)
        except Exception:
            log.error("part picture: bodies of " + _safe_name(o))
    return out


def _safe_name(occ):
    try:
        return occ.fullPathName
    except Exception:
        return "?"


def _comp_id(comp):
    try:
        return comp.id or comp.name
    except Exception:
        return comp.name


def thumb_key(occ):
    """The picture's key: component + its bodies' revisions ("" if it can't be read)."""
    if occ is None:
        return ""
    try:
        revs = []
        for body, _ in _bodies(occ):
            try:
                revs.append(body.revisionId)
            except Exception:
                revs.append(body.name)
        if not revs:
            return ""
        base = _comp_id(occ.component)
        return hashlib.sha1((base + "|" + "|".join(revs)).encode("utf-8")).hexdigest()[:16]
    except Exception:
        log.error("part picture key of " + _safe_name(occ))
        return ""


def _mesh(body):
    key = (_comp_id(body.parentComponent), body.name, getattr(body, "revisionId", ""))
    if key not in _meshes:
        calc = body.meshManager.createMeshCalculator()
        calc.setQuality(adsk.fusion.TriangleMeshQualityOptions.LowQualityTriangleMesh)
        mesh = calc.calculate()
        _meshes[key] = (list(mesh.nodeCoordinatesAsFloat), list(mesh.nodeIndices))
    return _meshes[key]


def mesh_for(occ):
    """{"p": [x, y, z, ...], "t": [i, j, k, ...]} of the part in its component's space, or None."""
    coords, tris = [], []
    try:
        for body, matrix in _bodies(occ):
            pts, idx = _mesh(body)
            m = [matrix.getCell(r, c) for r in range(3) for c in range(4)]   # rows of [R | t]
            base = len(coords) // 3
            for i in range(0, len(pts), 3):
                x, y, z = pts[i], pts[i + 1], pts[i + 2]
                coords.extend((round(m[0] * x + m[1] * y + m[2] * z + m[3], 3),
                               round(m[4] * x + m[5] * y + m[6] * z + m[7], 3),
                               round(m[8] * x + m[9] * y + m[10] * z + m[11], 3)))
            tris.extend(base + i for i in idx)
            if len(tris) // 3 > MAX_TRIANGLES * 2:
                break
    except Exception:
        log.error("part mesh")
        return None
    n = len(tris) // 3
    if n > MAX_TRIANGLES:               # keep every k-th triangle: a little speckled, fine at this size
        k = n / float(MAX_TRIANGLES)
        tris = [v for j in range(MAX_TRIANGLES) for v in tris[int(j * k) * 3:int(j * k) * 3 + 3]]
    return {"p": coords, "t": tris} if tris else None
