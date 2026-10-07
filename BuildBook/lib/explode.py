"""Explode math: vectors, distance modes, and parent-space conversion.

Pure Python on plain (x, y, z) tuples so it can be tested outside Fusion.
The Fusion-facing code converts Point3D/Vector3D/Matrix3D to tuples first.
"""

import math

UNIFORM = "uniform"
STACKED = "stacked"            # spaced out along the direction, ordered by position
STACKED_SELECTION = "stackedSelection"  # spaced out in selection order
MODES = (UNIFORM, STACKED, STACKED_SELECTION)

AXES = {
    "+X": (1.0, 0.0, 0.0), "-X": (-1.0, 0.0, 0.0),
    "+Y": (0.0, 1.0, 0.0), "-Y": (0.0, -1.0, 0.0),
    "+Z": (0.0, 0.0, 1.0), "-Z": (0.0, 0.0, -1.0),
}


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def length(a):
    return math.sqrt(dot(a, a))


def normalize(a):
    n = length(a)
    if n < 1e-12:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def is_zero(a, tol=1e-9):
    return length(a) < tol


LEVEL_TOL = 0.01                # cm: parts this close along the direction count as level (one tier)


def distances(mode, count, distance, centers=None, direction=None):
    """Per-part explode distance along the direction, in input order.

    UNIFORM: every part moves `distance`.
    STACKED: parts are ordered by their position along `direction` and moved
        distance, 2*distance, ... so the nearest part moves least and nothing
        crosses over. Parts level with each other along the direction (within
        LEVEL_TOL, e.g. a row of screws) share a tier and move the same distance.
    STACKED_SELECTION: same spacing, in the order the parts were given.
    """
    if count <= 0:
        return []
    if mode == UNIFORM:
        return [distance] * count
    if mode == STACKED and centers is not None and direction is not None:
        pos = [dot(centers[i], direction) for i in range(count)]
        order = sorted(range(count), key=lambda i: pos[i])
        # With a negative distance the far end of the stack moves least.
        if distance < 0:
            order.reverse()
        out = [0.0] * count
        tier, tier_start = 0, None
        for i in order:
            if tier_start is None or abs(pos[i] - tier_start) > LEVEL_TOL:
                tier += 1                   # a new level along the direction
                tier_start = pos[i]
            out[i] = distance * tier
        return out
    return [distance * (i + 1) for i in range(count)]


def away_axis(vec, tol=1e-6):
    """("X" / "Y" / "Z", negative?) of the world axis `vec` mostly points along, or None if it's
    (nearly) zero. Used to move parts away from the assembly's centre."""
    mags = [abs(v) for v in vec]
    if max(mags) < tol:
        return None
    i = mags.index(max(mags))
    return "XYZ"[i], vec[i] < 0


def to_parent(world_vec, axes):
    """World vector -> parent-component space. `axes` = (x, y, z) world unit axes of the parent."""
    if axes is None:
        return tuple(world_vec)
    return (dot(world_vec, axes[0]), dot(world_vec, axes[1]), dot(world_vec, axes[2]))


def to_world(parent_vec, axes):
    """Parent-component-space vector -> world."""
    if axes is None:
        return tuple(parent_vec)
    x, y, z = parent_vec
    return add(add(scale(axes[0], x), scale(axes[1], y)), scale(axes[2], z))


def centroid(points):
    if not points:
        return (0.0, 0.0, 0.0)
    n = float(len(points))
    return (sum(p[0] for p in points) / n,
            sum(p[1] for p in points) / n,
            sum(p[2] for p in points) / n)
