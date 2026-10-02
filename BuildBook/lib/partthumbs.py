"""Part thumbnails for the panel's lists: a coarse mesh of each part, drawn by the panel.

Nothing is rendered in Fusion's viewport (no isolating or camera moves): each part's bodies
are meshed at low quality, placed as they sit in the assembly, and sent to the panel, which
draws a small shaded isometric picture (palette/app.js partThumb) and keeps it in the browser
cache under the part's key. The key is the component (or split body) plus its bodies'
revision ids, so a part is drawn again only after it changes, and every copy of a part
shares one picture.
"""

import hashlib

import adsk.fusion

from . import log, refs

MAX_TRIANGLES = 4000          # per part: enough for a 48 px picture
_meshes = {}                  # body key -> (coords, indices) this session


def _bodies(part):
    """[(native body, Matrix3D placement)] of a part: its own bodies and those of its sub-parts."""
    if isinstance(part, refs.BodyPart):
        return [(part.body.nativeObject or part.body, part.occ.transform2)]
    out = []
    stack = [part]
    while stack:
        occ = stack.pop()
        try:
            for body in occ.bRepBodies:
                if body.isSolid:
                    out.append((body.nativeObject or body, occ.transform2))
            stack.extend(occ.childOccurrences)
        except Exception:
            pass
    return out


def thumb_key(part):
    """The part's picture key: component (or body) + its bodies' revisions."""
    if part is None:
        return ""
    try:
        base = refs.component_key(part.component) + ("#" + part.name if isinstance(part, refs.BodyPart) else "")
        revs = []
        for body, _ in _bodies(part)[:50]:
            try:
                revs.append(body.revisionId)
            except Exception:
                pass
        return hashlib.sha1((base + "|" + "|".join(revs)).encode("utf-8")).hexdigest()[:16]
    except Exception:
        return ""


def combine(base, keys):
    """An assembly's picture key from its parts' keys."""
    if not keys:
        return ""
    return hashlib.sha1((base + "|" + "|".join(sorted(keys))).encode("utf-8")).hexdigest()[:16]


def _mesh(body):
    key = (refs.component_key(body.parentComponent), body.name, getattr(body, "revisionId", ""))
    if key not in _meshes:
        calc = body.meshManager.createMeshCalculator()
        calc.setQuality(adsk.fusion.TriangleMeshQualityOptions.LowQualityTriangleMesh)
        mesh = calc.calculate()
        _meshes[key] = (list(mesh.nodeCoordinatesAsFloat), list(mesh.nodeIndices))
    return _meshes[key]


def mesh_for(part):
    """{"p": [x, y, z, ...], "t": [i, j, k, ...]} of the part in assembly space (rounded), or None."""
    coords, tris = [], []
    try:
        for body, matrix in _bodies(part):
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
    if n > MAX_TRIANGLES:               # keep every k-th triangle: a little speckled, fine at 48 px
        k = n / float(MAX_TRIANGLES)
        tris = [v for j in range(MAX_TRIANGLES) for v in tris[int(j * k) * 3:int(j * k) * 3 + 3]]
    return {"p": coords, "t": tris} if tris else None
