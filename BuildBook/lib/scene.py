"""Step renderer: draws a step's exploded view without touching the model.

Parts that need to move (current step) or fade (ghosted context) are drawn as
custom graphics from each body's display mesh, and the real occurrences are
hidden with the light bulb. `clear()` deletes the graphics and turns back on
exactly the occurrences this module turned off, so the user's own visibility
choices are preserved.

Fusion's light bulb hides a whole subtree, so when an occurrence is hidden
but one of its descendants should still show, that descendant is drawn as a
solid custom-graphics copy in place.
"""

import time

import adsk.core
import adsk.fusion

from . import explode, hidden_record, log, model, refs
from .overlay import CROP_GROUP_ID

EDGE_TOLERANCE_CM = 0.01
EDGE_COLOR = (30, 30, 30)
PREVIEW_COLOR = (70, 130, 230)
PREVIEW_OPACITY = 0.55
GHOST_COLOR = (205, 205, 205)
HIGHLIGHT_COLOR = (0, 120, 215)     # checked parts, like Fusion's selection blue
EDIT_EDGE_LIMIT = 6000          # edge points above which the explode dialog skips a body's edges
HOVER_COLOR = (0, 200, 255)         # explode dialog: the copy under the cursor (what a click picks)
TRAIL_OFF_COLOR = (160, 160, 160)   # hidden trail lines, only drawn while editing lines
TRAIL_HOVER_COLOR = (255, 120, 0)
SEGMENT_SEP = "|"
# Every custom-graphics group BuildBook creates carries this id, so leftovers
# can always be found and deleted. (Fusion's preview rollback can bring back a
# group we already deleted, which then outlives our reference to it.)
GROUP_ID = "BuildBook"

LINE_STYLES = {
    "dashed": adsk.fusion.LineStylePatterns.dashedLineStylePattern,
    "center": adsk.fusion.LineStylePatterns.centerLineStylePattern,
    "dotted": adsk.fusion.LineStylePatterns.dotLineStylePattern,
    "solid": adsk.fusion.LineStylePatterns.continuousLineStylePattern,
}

# Draw styles for a body copy.
SOLID = "solid"
GHOST = "ghost"
PREVIEW = "preview"


def _rgb(hex_color, fallback=(64, 64, 64)):
    try:
        h = hex_color.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return fallback


def _color(rgb, alpha=255):
    return adsk.core.Color.create(rgb[0], rgb[1], rgb[2], alpha)


def _ancestors(path):
    """'A:1+B:1+C:1' -> ['A:1', 'A:1+B:1'] (strict ancestors, outermost first)."""
    parts = path.split(model.PATH_SEP)
    return [model.PATH_SEP.join(parts[:i]) for i in range(1, len(parts))]


class Scene:
    def __init__(self):
        self._group = None
        # Everything this module has turned off, keyed so repeats don't pile up.
        # While `sticky` (an explode command is running), entries survive
        # resets: Fusion rolls back preview transactions, which can re-hide
        # parts after we restored them, so the final restore must cover all.
        self._hidden_occs = {}
        self._hidden_bodies = {}
        self.sticky = False
        self._mesh_cache = {}
        self._edge_cache = {}
        self.step_id = None
        self.edit = False
        self.upto = None            # explode move id the view stops after (None = all moves)
        # Trail line segments of the last render: id -> {path, index, start, end, on}.
        self.segments = {}
        self._segment_entities = {}
        self._segment_states = {}   # segment id -> "on" / "off" / "hover" as drawn
        self._trail_settings = {}
        self.lines_editing = False
        self.pick_hidden = False    # Edit lines: hidden (faint) lines clickable too
        self._pickable = set()      # paths of copies clickable in the explode command
        self._copy_groups = {}      # path -> its copy's graphics group (explode command)
        self.copy_places = []       # [(path, occurrence, world offset)] of drawn copies (explode command)
        self._bodies = []           # (leaf path, mesh entity, its normal colour) of drawn copies
        self.highlighted = set()    # part paths shown highlighted (checked in the panel)
        self.hover = None           # explode dialog: part whose copies draw as hovered

    # ------------------------------------------------------------ public

    @property
    def active(self):
        return self.step_id is not None

    def clear(self):
        """Remove all graphics and restore visibility. Leaves the model as the user had it."""
        self._reset()
        self.step_id = None
        self.edit = False
        self.upto = None

    def end_edit(self):
        """Leave sticky mode; the next reset restores everything touched during the edit."""
        self.sticky = False

    def flush_cache(self):
        self._mesh_cache.clear()
        self._edge_cache.clear()

    def show(self, design, manual, step_id, edit=False, current=None, upto=None, hover=None, light=False):
        """Render a step, running its explode moves in order.

        `upto` stops after that explode move (the panel's "state up to this
        move"). In `edit` mode (the explode command, which passes a working
        copy of the manual) moved copies are pickable (their graphics id is
        the occurrence path) and parts in `current` draw as a blue preview.
        """
        # Never switch everything back on first: each light-bulb change is a model change, and
        # doing all of them on every render made dragging in the explode dialog take ~0.6 s a
        # frame. _apply_visibility checks the real light bulbs instead (Fusion's preview
        # rollback can change them behind our back) and only switches what differs.
        _, step = model.find_step(manual, step_id)
        step = model.effective_step(manual, step)       # (the exploded view: every step's moves too)
        if step is None:
            self._reset(restore=False)
            self._restore_visibility()
            self.step_id = None
            return
        self.step_id = step_id
        self.edit = edit
        self.upto = upto if model.find_explode(step, upto) else None

        self.light = light              # while the explode arrow is dragged: no edges on other parts
        with log.timed("scene.show " + step["title"]):
            self.hover = hover          # a part (path) whose copies draw highlighted
            self._render(design, manual, step, edit, set(current or ()))

    # ------------------------------------------------------------ internals

    def _reset(self, restore=True):
        """Delete the drawn graphics and, with `restore`, turn every part we hid back on.

        Step-to-step renders pass restore=False and only change the parts
        whose visibility differs (see _apply_visibility): each light-bulb
        change is a model change in Fusion, and switching them all off and on
        again made steps take seconds to show.
        """
        try:
            if self._group is not None and self._group.isValid:
                self._group.deleteMe()
        except Exception:
            log.error("delete graphics group")
        self._group = None
        design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
        if design is not None:
            sweep(design, ids=(GROUP_ID, "BuildBookAnchor"))   # the crop overlay manages itself
        self.segments = {}
        self._segment_entities = {}
        self._segment_states = {}
        self.pick_hidden = False
        self._pickable = set()
        self._copy_groups = {}
        self.copy_places = []
        self._bodies = []
        self._made = []             # entities drawn by the latest _draw_bodies call
        self._anim = []             # (occurrence, owner paths, its drawn entities): set_progress moves them
        self._anim_moves = {}       # owner path -> [(world vector, index of its move)]
        self.anim_moves = 0         # moves in the step shown (set_progress runs 0 .. this)
        self._anim_order = {}       # explode id -> its index (trail lines grow with their move)
        self._anim_p = None         # set_progress's last p, offsets per copy and trail fractions
        self._anim_last = {}
        self._anim_trail = {}
        self._anim_base = {}        # copy index -> (its placement as an array, translation x, y, z)
        self._edges_off = set()     # copy indices whose edge lines are hidden while they move
        self._fast = None           # what the last render worked out, for update_offsets
        self.fast_stats = ""
        self.anim_stats = (0, 0.0, 0, 0.0)
        self.lines_editing = False
        if restore:
            self._restore_visibility()

    def _restore_visibility(self):
        for occ in list(self._hidden_occs.values()):
            try:
                if occ.isValid and not occ.isLightBulbOn:
                    occ.isLightBulbOn = True
            except Exception:
                log.error("restore occurrence visibility")
        for body in list(self._hidden_bodies.values()):
            try:
                if body.isValid and not body.isLightBulbOn:
                    body.isLightBulbOn = True
            except Exception:
                log.error("restore body visibility")
        if not self.sticky:
            self._hidden_occs = {}
            self._hidden_bodies = {}
        self._save_record()

    def _save_record(self):
        """Keep the on-disk record of what we hid current (see hidden_record: crash safety)."""
        try:
            design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
            if design is None:
                return
            occs = [p for p, o in self._hidden_occs.items() if o.isValid and not o.isLightBulbOn]
            bodies = []
            for b in self._hidden_bodies.values():
                if b.isValid and not b.isLightBulbOn:
                    entry = hidden_record.body_entry(b)
                    if entry is not None:
                        bodies.append(entry)
            hidden_record.record(design, "scene", occs, bodies)
        except Exception:
            log.error("save the hidden-parts record")

    def _render(self, design, manual, step, edit, editing):
        settings = manual["settings"]
        earlier_mode, later_mode, unassigned_mode = model.context_modes(manual, step)
        index = refs.path_index(design)

        # Current-step parts: path -> {occ, moves [(world vector, trail, explode id)], world total}.
        occs = {}
        refs_to_resolve = [i["ref"] for i in step["items"]]
        refs_to_resolve += [p["ref"] for ex in step.get("explodes", []) for p in ex["parts"]]
        for ref in refs_to_resolve:
            occ, _ = refs.resolve(design, ref, index)
            if occ is not None:
                occs[occ.fullPathName] = occ
                ref["path"] = occ.fullPathName
        centers = {path: refs.bbox_center(occ) for path, occ in occs.items()}
        anchors = {i["ref"].get("path"): i.get("anchor") for i in step["items"] if i.get("anchor")}
        moves = model.evaluate(manual, step, centers, self.upto)
        current = {path: _entry(occ, moves.get(path, [])) for path, occ in occs.items()}

        # Decide what each occurrence with its own visible bodies should look like.
        mode_for = {model.EARLIER: earlier_mode, model.LATER: later_mode, model.UNASSIGNED: unassigned_mode}
        plans = []          # (occ, bodies, desired, world_offset)
        places = []         # explode dialog: where each drawn copy is (copy_places)
        for path, occ in index.items():
            if refs.is_split(occ):
                continue                # its bodies are parts of their own (BodyPart entries)
            if not self._user_visible(occ):
                continue
            ours = any(p in self._hidden_occs for p in [*_ancestors(path), path])
            if isinstance(occ, refs.BodyPart) and path in self._hidden_occs:
                # A split body we hid: its light bulb is the one we switched off, so it's still
                # this part's body (skipping it would count it as not ours and switch it back on).
                bodies = [occ.body]
            else:
                bodies = [b for b in occ.bRepBodies if (b.isLightBulbOn if ours else b.isVisible)]
            if not bodies:
                continue
            owners = [p for p in [*_ancestors(path), path] if p in current]
            if owners:
                offset = (0.0, 0.0, 0.0)
                for p in owners:
                    offset = explode.add(offset, current[p]["world"])
                if explode.is_zero(offset):
                    desired = model.SHOWN
                elif edit and any(p in editing for p in owners):
                    desired = PREVIEW
                else:
                    desired = "exploded"
            else:
                offset = (0.0, 0.0, 0.0)
                desired = mode_for[model.leaf_state(manual, step["id"], path)]
            plans.append((occ, bodies, desired, offset))
            if edit and desired in ("exploded", PREVIEW):
                places.append((path, occ, offset))

        # Root-component bodies count as unassigned.
        root_bodies = [b for b in design.rootComponent.bRepBodies
                       if b.isVisible or b.entityToken in self._hidden_bodies]

        # Trail line anchors must be read while everything is still at home.
        segments = self._trail_segments(current, anchors)

        # Moved parts are hidden at home; their drawn copies stand for them (in the explode
        # dialog the copies are clickable: blue = in the move being edited).
        hide_paths = {occ.fullPathName for occ, _, desired, _ in plans
                      if desired in ("exploded", PREVIEW, model.HIDDEN, model.GHOSTED)}
        hide_bodies = {b.entityToken: b for b in root_bodies} if unassigned_mode != model.SHOWN else {}
        occ_by_path = {occ.fullPathName: occ for occ, _, _, _ in plans}
        hide_top = {p: occ_by_path[p] for p in hide_paths
                    if not any(a in hide_paths for a in _ancestors(p))}

        # (Every render is a full rebuild: inside the explode dialog Fusion's preview rollback
        # removes what the previous preview drew, so there's nothing to move in place.)
        self._reset(restore=False)
        self.segments, self.copy_places = segments, places
        order = {ex["id"]: k for k, ex in enumerate(step.get("explodes", []))}
        self._anim_moves = {p: [(vec, order.get(eid, 0)) for vec, _, eid in e["moves"]]
                            for p, e in current.items()}
        self.anim_moves = (order.get(self.upto, len(order) - 1) + 1) if order else 0
        self._anim_order = order
        self._group = design.rootComponent.customGraphicsGroups.add()
        self._group.id = GROUP_ID
        ghost = self._ghost_effect(settings)
        draw_edges = settings.get("drawEdges", True) and not getattr(self, "light", False)

        drawn = 0
        for occ, bodies, desired, offset in plans:
            path = occ.fullPathName
            hidden_by_parent = any(a in hide_paths for a in _ancestors(path))
            if desired in ("exploded", PREVIEW) and self.hover and model.is_self_or_ancestor(self.hover, path):
                # Under the cursor in the explode dialog: bright, outlined (PREVIEW draws the outline).
                self._made = []
                drawn += self._draw_bodies(occ, bodies, offset, PREVIEW, None, False, outline=(HOVER_COLOR, 3.0))
                owners = [p for p in [*_ancestors(path), path] if p in current]
                self._anim.append((occ, owners, list(self._made)))
            elif desired == "exploded":
                self._made = []
                drawn += self._draw_bodies(occ, bodies, offset, SOLID, None, draw_edges)
                owners = [p for p in [*_ancestors(path), path] if p in current]
                self._anim.append((occ, owners, list(self._made)))
            elif desired == PREVIEW:
                self._made = []
                drawn += self._draw_bodies(occ, bodies, offset, PREVIEW, None, False, outline=(HIGHLIGHT_COLOR, 2.0))
                owners = [p for p in [*_ancestors(path), path] if p in current]
                self._anim.append((occ, owners, list(self._made)))
            elif desired == model.GHOSTED:
                drawn += self._draw_bodies(occ, bodies, offset, GHOST, ghost, False)
            elif desired == model.SHOWN and hidden_by_parent:
                drawn += self._draw_bodies(occ, bodies, offset, SOLID, None, draw_edges)

        if unassigned_mode == model.GHOSTED:
            for body in root_bodies:
                drawn += self._draw_bodies(None, [body], (0.0, 0.0, 0.0), GHOST, ghost, False)

        self._draw_trails(dict(settings, trail=model.trail_look(manual, step)))

        # Hide last, after every query that depends on visibility. Only the top
        # of each hidden branch needs its light bulb off.
        shown, hidden = self._apply_visibility(hide_top, hide_bodies)
        self._fast = {"step_id": step["id"], "occs": occs, "centers": centers, "anchors": anchors,
                      "hide_top": hide_top, "hide_bodies": hide_bodies,
                      "moving": {p for p, e in current.items() if not explode.is_zero(e["world"])}}

        log.info("scene: {} occurrences, {} drawn bodies, {} hidden ({} turned on, {} turned off), {} trails".format(
            len(plans), drawn, len(self._hidden_occs), shown, hidden, len(self.segments)))

    def set_progress(self, p, together=False, fast=True):
        """Animation: put the drawn copies where they are `p` moves into the step (0 = assembled,
        anim_moves = fully exploded; a fraction is part way along that move), and grow / shrink
        each trail line with its move. Only what changed since the last frame is touched (setting
        every copy's position each frame made steps with many parts lag)."""
        if together and self.anim_moves:
            # All the moves at once: each is the same fraction of the way, p / moves.
            frac = min(1.0, max(0.0, p / float(self.anim_moves)))
            amount = lambda k: frac                                     # noqa: E731
        else:
            amount = lambda k: min(1.0, max(0.0, p - k))                # noqa: E731
        if (p, together) == self._anim_p:
            self._show_edges(set())         # (paused: every part's edges back)
            return
        self._anim_p = (p, together)
        moving = set()
        t0 = time.perf_counter()
        moved = 0
        for i, (occ, owners, entities) in enumerate(self._anim):
            offset = (0.0, 0.0, 0.0)
            for owner in owners:
                for vec, k in self._anim_moves.get(owner, ()):
                    f = amount(k)
                    if f > 0:
                        offset = explode.add(offset, explode.scale(vec, f))
            key = tuple(round(v, 5) for v in offset)
            if self._anim_last.get(i) == key:
                continue
            moving.add(i)
            self._anim_last[i] = key
            # The occurrence's own placement is read once per step (reading it is slow for parts
            # deep in sub-assemblies), then only the offset changes.
            base = self._anim_base.get(i)
            if base is None:
                m = occ.transform2 if occ is not None else adsk.core.Matrix3D.create()
                base = self._anim_base[i] = (m.asArray(), m.translation.x, m.translation.y, m.translation.z)
            matrix = adsk.core.Matrix3D.create()
            matrix.setWithArray(base[0])
            matrix.translation = adsk.core.Vector3D.create(base[1] + offset[0], base[2] + offset[1], base[3] + offset[2])
            for entity in entities:
                try:
                    entity.transform = matrix
                    moved += 1
                except Exception:
                    pass
        self._show_edges(moving if fast else set())        # (a recording keeps every edge)
        t1 = time.perf_counter()
        trails = 0
        style = self._trail_settings.get("style", "dashed")
        dash = getattr(self, "_dash", 0.25)
        homes = {}                      # (together) each part's first trail start: the lines scale from there
        if together:
            for seg in sorted(self.segments.values(), key=lambda s: self._anim_order.get(s["explode"], 0)):
                homes.setdefault(seg["path"], seg["start"])
        for seg_id, lines in self._segment_entities.items():
            seg = self.segments.get(seg_id)
            if seg is None:
                continue
            f = round(amount(self._anim_order.get(seg["explode"], 0)), 4)
            if self._anim_trail.get(seg_id) == f:
                continue
            self._anim_trail[seg_id] = f
            try:
                if f <= 0:
                    lines.isVisible = False
                    continue
                start = seg["start"]
                end = explode.add(start, explode.scale(explode.sub(seg["end"], start), f))
                if together:            # every earlier move is part way too: scale from home
                    home = homes.get(seg["path"], start)
                    start = explode.add(home, explode.scale(explode.sub(seg["start"], home), f))
                    end = explode.add(home, explode.scale(explode.sub(seg["end"], home), f))
                lines.coordinates.coordinates = dash_coords(start, end, style, dash)
                lines.isVisible = True
                trails += 1
            except Exception:
                log.error("animate trail line")
        self.anim_stats = (moved, (t1 - t0) * 1000, trails, (time.perf_counter() - t1) * 1000)

    def show_all_edges(self):
        self._show_edges(set())

    def _show_edges(self, moving):
        """Edge lines of parts on the move are hidden until they stop: Fusion redraws a moved copy's
        edges from scratch every frame, and parts with many edges (gear teeth, racks) took ~100 ms
        a frame. Their shaded faces still move smoothly."""
        for i in self._edges_off - moving:
            for lines in self._anim_edges(i):
                lines.isVisible = True
        for i in moving - self._edges_off:
            for lines in self._anim_edges(i):
                lines.isVisible = False
        self._edges_off = set(moving)

    def _anim_edges(self, i):
        return [e for e in self._anim[i][2] if e.objectType == adsk.fusion.CustomGraphicsLines.classType()]

    def _user_visible(self, occ):
        """Visible as the user left it: light bulbs we switched off ourselves don't count."""
        o = occ
        while o is not None:
            if o.fullPathName not in self._hidden_occs and not o.isLightBulbOn:
                return False
            o = o.assemblyContext
        return occ.isVisible or any(p in self._hidden_occs for p in [*_ancestors(occ.fullPathName),
                                                                       occ.fullPathName])

    def _apply_visibility(self, hide_occs, hide_bodies):
        """Make exactly `hide_occs` / `hide_bodies` hidden by us, touching only what changes.

        Returns (turned on, turned off) counts.

        In sticky mode (explode dialog) the record of touched parts only grows (so closing
        the dialog puts back everything it ever touched), and the real light bulbs are
        checked both ways, since Fusion's preview rollback can switch them behind our back.
        """
        shown = hidden = 0
        if self.sticky:
            for path, occ in self._hidden_occs.items():
                if path not in hide_occs:
                    try:
                        if occ.isValid and not occ.isLightBulbOn:
                            occ.isLightBulbOn = True
                            shown += 1
                    except Exception:
                        log.error("restore occurrence visibility")
            for token, body in self._hidden_bodies.items():
                if token not in hide_bodies:
                    try:
                        if body.isValid and not body.isLightBulbOn:
                            body.isLightBulbOn = True
                            shown += 1
                    except Exception:
                        log.error("restore body visibility")
            for path, occ in hide_occs.items():
                if occ.isLightBulbOn:
                    occ.isLightBulbOn = False
                    hidden += 1
                self._hidden_occs[path] = occ
            for token, body in hide_bodies.items():
                if body.isLightBulbOn:
                    body.isLightBulbOn = False
                    hidden += 1
                self._hidden_bodies[token] = body
            self._save_record()
            return shown, hidden
        for path, occ in list(self._hidden_occs.items()):
            if path not in hide_occs:
                try:
                    if occ.isValid and not occ.isLightBulbOn:
                        occ.isLightBulbOn = True
                        shown += 1
                except Exception:
                    log.error("restore occurrence visibility")
                del self._hidden_occs[path]
        # Check the real light bulb, not our record: Fusion's preview rollback
        # (explode dialog) can turn parts back on behind our back.
        for path, occ in hide_occs.items():
            if occ.isLightBulbOn:
                occ.isLightBulbOn = False
                hidden += 1
            self._hidden_occs[path] = occ
        for token, body in list(self._hidden_bodies.items()):
            if token not in hide_bodies:
                try:
                    if body.isValid and not body.isLightBulbOn:
                        body.isLightBulbOn = True
                        shown += 1
                except Exception:
                    log.error("restore body visibility")
                del self._hidden_bodies[token]
        for token, body in hide_bodies.items():
            if body.isLightBulbOn:
                body.isLightBulbOn = False
                hidden += 1
            self._hidden_bodies[token] = body
        self._save_record()
        return shown, hidden

    def _trail_segments(self, current, anchors):
        """One segment per move, chained end to start, whether its line is on or off.

        Lines start at the part's anchor (its chosen line start point) if it
        has one, else at the centre of its bounding box.
        """
        segments = {}
        for path, entry in current.items():
            parent_offset = (0.0, 0.0, 0.0)
            for anc in _ancestors(path):
                if anc in current:
                    parent_offset = explode.add(parent_offset, current[anc]["world"])
            start = explode.add(_anchor_point(entry["occ"], anchors.get(path)), parent_offset)
            for vec, trail, explode_id in entry["moves"]:
                if explode.is_zero(vec):
                    continue
                end = explode.add(start, vec)
                seg_id = segment_id(path, explode_id)
                segments[seg_id] = {"id": seg_id, "path": path, "explode": explode_id,
                                    "start": start, "end": end, "on": bool(trail)}
                start = end
        return segments

    def _ghost_effect(self, settings):
        opacity = float(settings.get("ghostOpacity", 0.2))
        return adsk.fusion.CustomGraphicsBasicMaterialColorEffect.create(
            _color(GHOST_COLOR), _color(GHOST_COLOR), _color((255, 255, 255)),
            _color((0, 0, 0)), 10.0, opacity)

    def _placement(self, occ, offset):
        matrix = occ.transform2 if occ is not None else adsk.core.Matrix3D.create()
        t = matrix.translation
        matrix.translation = adsk.core.Vector3D.create(t.x + offset[0], t.y + offset[1], t.z + offset[2])
        return matrix

    def _draw_bodies(self, occ, bodies, offset, style, effect, draw_edges, outline=(None, 2.0)):
        matrix = self._placement(occ, offset)
        # Pickable copies (explode command) get a sub-group per part, id = its
        # path, so whatever Fusion reports for a click resolves to one part.
        pickable = self.edit and occ is not None and style in (SOLID, PREVIEW)
        target = self._group
        if pickable:
            target = self._group.addGroup()
            target.id = occ.fullPathName
            self._pickable.add(occ.fullPathName)
            self._copy_groups[occ.fullPathName] = target
        count = 0
        for body in bodies:
            native = body.nativeObject or body
            mesh = self._mesh(native)
            if mesh is None:
                continue
            coords, indices, normals = mesh
            entity = target.addMesh(
                adsk.fusion.CustomGraphicsCoordinates.create(coords), indices, normals, indices)
            entity.transform = matrix
            self._made.append(entity)
            entity.isSelectable = pickable
            if occ is not None:
                entity.id = occ.fullPathName
            # The explode dialog draws no edge lines at all: they were most of each redraw's cost, and
            # Fusion redraws there on every change. Picked parts are tinted blue instead of outlined.
            # Pictures and normal step views keep every edge.
            heavy = self.edit
            if heavy and style == PREVIEW:
                tint = _color(outline[0] or HIGHLIGHT_COLOR)
                effect = adsk.fusion.CustomGraphicsBasicMaterialColorEffect.create(
                    tint, tint, _color((255, 255, 255)), _color((0, 0, 0)), 20.0, 1.0)
            entity.color = effect if effect is not None else self._appearance_effect(occ, body)
            if occ is not None and style != GHOST:
                self._bodies.append((occ.fullPathName, entity, entity.color))
            count += 1

            if style == PREVIEW and not heavy:
                # The move being edited: shaded, edges in selection blue so its parts read as picked
                # (cyan and thicker for the one under the cursor).
                edges = self._edges(native)
                if edges and edges[1]:
                    lines = target.addLines(
                        adsk.fusion.CustomGraphicsCoordinates.create(edges[0]), [], True, edges[1])
                    lines.transform = matrix
                    self._made.append(lines)
                    lines.isSelectable = False
                    lines.weight = outline[1]
                    lines.depthPriority = 1
                    lines.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(_color(outline[0] or HIGHLIGHT_COLOR))
            if draw_edges and style == SOLID and not heavy:
                edges = self._edges(native)
                if edges and edges[1]:
                    lines = target.addLines(
                        adsk.fusion.CustomGraphicsCoordinates.create(edges[0]), [], True, edges[1])
                    lines.transform = matrix
                    self._made.append(lines)
                    lines.isSelectable = False
                    lines.weight = 1.0
                    lines.depthPriority = 1
                    lines.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(_color(EDGE_COLOR))
        return count

    def _edge_points(self, native):
        edges = self._edges(native)
        return len(edges[0]) // 3 if edges else 0

    def _appearance_effect(self, occ, body):
        appearance = None
        try:
            appearance = (occ.appearance if occ is not None else None) or body.appearance
        except Exception:
            pass
        if appearance is not None:
            effect = adsk.fusion.CustomGraphicsAppearanceColorEffect.create(appearance)
            if effect:
                return effect
        return adsk.fusion.CustomGraphicsSolidColorEffect.create(_color((170, 170, 170)))

    def _cache_key(self, native):
        try:
            return (native.entityToken, native.revisionId)
        except Exception:
            return None

    def _mesh(self, native):
        key = self._cache_key(native)
        if key is not None and key in self._mesh_cache:
            return self._mesh_cache[key]
        try:
            tri = native.meshManager.displayMeshes.bestMesh
            if tri is None:
                return None
            nodes = list(tri.nodeIndices)
            data = (list(tri.nodeCoordinatesAsDouble), nodes, list(tri.normalVectorsAsDouble))
        except Exception:
            log.error("mesh for body " + getattr(native, "name", "?"))
            return None
        if key is not None:
            self._mesh_cache[key] = data
        return data

    def _edges(self, native):
        key = self._cache_key(native)
        if key is not None and key in self._edge_cache:
            return self._edge_cache[key]
        coords, lengths = [], []
        try:
            for edge in native.edges:
                ev = edge.evaluator
                if ev is None:
                    continue                # (some edges have none: skip them, keep the rest)
                ok, start, end = ev.getParameterExtents()
                if not ok:
                    continue
                ok, points = ev.getStrokes(start, end, EDGE_TOLERANCE_CM)
                if not ok or len(points) < 2:
                    continue
                for p in points:
                    coords.extend((p.x, p.y, p.z))
                lengths.append(len(points))
        except Exception:
            log.error("edges for body " + getattr(native, "name", "?"))
        data = (coords, lengths)
        if key is not None:
            self._edge_cache[key] = data
        return data

    # ------------------------------------------------------------ trail lines

    def set_highlight(self, paths):
        """Colour the drawn copies of `paths` (or parts inside them) like a selection.

        Exploded parts are hidden real parts plus drawn copies, and hidden parts
        can't be selected in Fusion, so checked parts are shown this way. Only
        colours change; nothing is redrawn.
        """
        self.highlighted = set(paths)
        if not self._bodies:
            return
        highlight = adsk.fusion.CustomGraphicsBasicMaterialColorEffect.create(
            _color(HIGHLIGHT_COLOR), _color(HIGHLIGHT_COLOR), _color((255, 255, 255)),
            _color((0, 0, 0)), 30.0, 1.0)
        for leaf, entity, normal in self._bodies:
            try:
                if not entity.isValid:
                    continue
                on = any(model.is_self_or_ancestor(p, leaf) for p in self.highlighted)
                entity.color = highlight if on else normal
            except Exception:
                log.error("highlight")

    def copy_entities(self, path):
        """The drawn copy of a part (its group, then its first mesh), for a selection box."""
        group = self._copy_groups.get(path)
        out = []
        try:
            if group is not None and group.isValid:
                out.append(group)
                if group.count:
                    out.append(group.item(0))
        except Exception:
            pass
        return out

    def pickable_path(self, entity):
        """Occurrence path for a clicked explode-command copy, or None.

        Walks up from the reported entity (mesh, part sub-group) to the first
        id that is a part path; the scene's top group doesn't count, so a
        click reported as "the whole overlay" picks nothing.
        """
        ent = adsk.fusion.CustomGraphicsEntity.cast(entity)
        while ent is not None:
            if ent.id and ent.id not in (GROUP_ID, CROP_GROUP_ID) and ent.id in self._pickable:
                return ent.id
            ent = adsk.fusion.CustomGraphicsEntity.cast(ent.parent)
        return None

    def _draw_trails(self, settings):
        self._trail_settings = settings.get("trail", {})
        # Dash length for drawn patterns: a fraction of the model's size (Fusion's own line style
        # patterns didn't show on these lines: every style drew solid).
        try:
            box = adsk.core.Application.get().activeProduct.rootComponent.boundingBox
            size = box.minPoint.distanceTo(box.maxPoint)
        except Exception:
            size = 30.0
        self._dash = max(size / 120.0, 0.05) * float(self._trail_settings.get("scale", 1.0))
        for seg in self.segments.values():
            if seg["on"]:
                self._add_segment(seg, "on")

    def _add_segment(self, seg, state):
        style = "dotted" if state == "off" else self._trail_settings.get("style", "dashed")
        coords = dash_coords(seg["start"], seg["end"], style, getattr(self, "_dash", 0.25))
        lines = self._group.addLines(adsk.fusion.CustomGraphicsCoordinates.create(coords), [], False)
        lines.id = seg["id"]
        lines.isScreenSpaceLineStyle = True
        self._segment_entities[seg["id"]] = lines
        self._style(lines, state)
        return lines

    def _style(self, lines, state):
        """state: "on" (normal), "off" (hidden line, faint while editing lines), "hover"."""
        trail = self._trail_settings
        weight = float(trail.get("weight", 1.5))
        color = _rgb(trail.get("color", "#404040"))
        if state == "off":
            weight, color = 1.0, TRAIL_OFF_COLOR
        elif state == "hover":
            weight, color = weight + 2.5, TRAIL_HOVER_COLOR
        lines.weight = weight
        # (the pattern is drawn as separate dashes: see dash_coords)
        lines.lineStylePattern = LINE_STYLES["solid"]
        lines.lineStyleScale = float(trail.get("scale", 1.0))
        lines.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(_color(color))
        # While editing lines, shown lines are clickable; hidden ones only on request,
        # so overlapping lines resolve to a shown one.
        lines.isSelectable = False      # (the line editor finds lines from its own mouse events)
        lines.depthPriority = 1 if state == "on" else 0
        self._segment_states[lines.id] = state

    def start_lines_edit(self):
        """Make every trail segment hoverable; hidden ones draw faint so they can be turned back on."""
        self.lines_editing = True
        if self._group is None or not self._group.isValid:
            return
        for seg in self.segments.values():
            entity = self._segment_entities.get(seg["id"])
            if entity is None:
                self._add_segment(seg, "off")

    def set_pick_hidden(self, flag):
        """Edit lines: make hidden (faint) lines clickable or not."""
        self.pick_hidden = bool(flag)     # (the line editor's click search includes hidden lines)

    def update_offsets(self, manual, step_id):
        """Explode dialog, dragging the arrow: move the drawn copies and trail lines to the new
        distance instead of drawing the whole step again (that took ~0.4 s a frame on big steps).
        Only when nothing but distances changed; returns False when a full redraw is needed."""
        fast = self._fast
        if not fast or fast["step_id"] != step_id or self._group is None or not self._group.isValid:
            return self._no("nothing drawn to move" if not fast else "drawing gone")
        t = [time.perf_counter()]
        _, step = model.find_step(manual, step_id)
        step = model.effective_step(manual, step)
        if step is None:
            return self._no("no step")
        moves = model.evaluate(manual, step, fast["centers"], self.upto)
        current = {path: _entry(occ, moves.get(path, [])) for path, occ in fast["occs"].items()}
        if {p for p, e in current.items() if not explode.is_zero(e["world"])} != fast["moving"]:
            return self._no("a part starts / stops moving")
        t.append(time.perf_counter())
        segments = self._trail_segments(current, fast["anchors"])
        if set(segments) != set(self.segments):
            return self._no("trail lines changed")
        t.append(time.perf_counter())
        moved = set()
        for i, (occ, owners, entities) in enumerate(self._anim):
            offset = (0.0, 0.0, 0.0)
            for owner in owners:
                offset = explode.add(offset, current[owner]["world"])
            key = tuple(round(v, 6) for v in offset)
            if self._anim_last.get(i) == key:
                continue
            if i in self._anim_last:
                moved.add(i)
            self._anim_last[i] = key
            matrix = self._placement(occ, offset)
            for entity in entities:
                try:
                    entity.transform = matrix
                except Exception:
                    return self._no("a copy can't be moved")
        style = self._trail_settings.get("style", "dashed")
        dash = getattr(self, "_dash", 0.25)
        for seg_id, seg in segments.items():
            entity = self._segment_entities.get(seg_id)
            old = self.segments.get(seg_id)
            if entity is None or old is None or (old["start"], old["end"]) == (seg["start"], seg["end"]):
                continue
            try:
                entity.coordinates.coordinates = dash_coords(seg["start"], seg["end"], style, dash)
            except Exception:
                return self._no("a trail line can't be moved")
        for seg_id, seg in segments.items():
            seg["on"] = self.segments[seg_id].get("on", seg["on"])
        self.segments = segments
        # The moving parts' edges are hidden until the drag ends (Fusion redraws a moved part's
        # edges from scratch every frame: most of the time of each step of the drag).
        self._show_edges(moved | self._edges_off)     # (back with show_all_edges when the drag ends)
        t.append(time.perf_counter())
        # Fusion's preview rollback switches parts back on: hide the same ones again (only what differs).
        shown, hidden = self._apply_visibility(fast["hide_top"], fast["hide_bodies"])
        t.append(time.perf_counter())
        self.fast_stats = "moves {:.0f}, trail points {:.0f}, copies+lines {:.0f}, visibility {:.0f} ({} re-hidden)".format(
            *[(b - a) * 1000 for a, b in zip(t, t[1:])], hidden)
        return True

    def _no(self, why):
        if why != getattr(self, "_no_logged", None):
            self._no_logged = why
            log.info("explode: full redraw ({})".format(why))
        return False

    def tint_hover(self, path):
        """Explode dialog: the drawn copies of `path` (a part or assembly) turn cyan, the rest back
        to their own colour. Colours only (no redraw); the next redraw keeps it (self.hover)."""
        self.hover = path
        cyan = adsk.fusion.CustomGraphicsBasicMaterialColorEffect.create(
            _color(HOVER_COLOR), _color(HOVER_COLOR), _color((255, 255, 255)), _color((0, 0, 0)), 30.0, 1.0)
        for leaf, entity, normal in self._bodies:
            try:
                if entity.isValid:
                    entity.color = cyan if path and model.is_self_or_ancestor(path, leaf) else normal
            except Exception:
                pass

    def highlight_trails(self, explode_id):
        """Trail lines of one move drawn highlighted (None: all back to normal)."""
        for seg_id, entity in self._segment_entities.items():
            seg = self.segments.get(seg_id)
            if seg is None or not entity.isValid:
                continue
            want = "hover" if explode_id is not None and seg["explode"] == explode_id else "on"
            if self._segment_states.get(seg_id) != want:
                self._style(entity, want)

    def style_segment(self, seg_id, state):
        entity = self._segment_entities.get(seg_id)
        if entity is not None and entity.isValid:
            self._style(entity, state)

    def replace_segment(self, seg_id, state):
        """Draw a segment again as a new line in `state`. Restyling the clicked line itself
        doesn't stick: Fusion puts back the look it had when its hover / selection began,
        and a new line has no such memory."""
        seg = self.segments.get(seg_id)
        if seg is None or self._group is None or not self._group.isValid:
            return
        old = self._segment_entities.pop(seg_id, None)
        try:
            if old is not None and old.isValid:
                old.deleteMe()
        except Exception:
            log.error("replace trail line")
        self._add_segment(seg, state)


# On / off lengths of each style, in dash lengths (on, off, on, off, ...).
DASH_PATTERNS = {"dashed": (1.0, 0.6), "dotted": (0.12, 0.5), "center": (2.0, 0.4, 0.4, 0.4)}


def dash_coords(start, end, style, dash):
    """Flat coordinates for a trail from start to end as separate line pieces (pairs of points)
    in the style's pattern; solid (or an unknown style): the one line."""
    pattern = DASH_PATTERNS.get(style)
    length = sum((b - a) ** 2 for a, b in zip(start, end)) ** 0.5
    if not pattern or length <= 0 or dash <= 0 or length / dash > 4000:
        return list(start) + list(end)
    unit = [(b - a) / length for a, b in zip(start, end)]
    out, at, i = [], 0.0, 0
    while at < length:
        step = pattern[i % len(pattern)] * dash
        if i % 2 == 0:                      # a dash (the odd entries are gaps)
            stop = min(at + step, length)
            out += [start[k] + unit[k] * at for k in range(3)] + [start[k] + unit[k] * stop for k in range(3)]
        at += step
        i += 1
    return out


def sweep(design, everything=False, ids=(GROUP_ID, CROP_GROUP_ID, "BuildBookAnchor", "BuildBookPickHighlight",
                                         "BuildBookBoxSelect")):
    """Delete BuildBook's custom-graphics groups in a design (all groups with `everything`).

    Returns how many were removed. `everything` is for leftovers from before
    groups were tagged; custom graphics never hold model data, so removing
    them is safe.
    """
    removed = 0
    components = [design.rootComponent] if not everything else list(design.allComponents)
    for comp in components:
        try:
            groups = comp.customGraphicsGroups
            for i in range(groups.count - 1, -1, -1):
                group = groups.item(i)
                if everything or group.id in ids:
                    group.deleteMe()
                    removed += 1
        except Exception:
            log.error("sweep graphics in " + comp.name)
    if removed:
        log.info("swept {} graphics group(s)".format(removed))
    return removed


def visibility_report(design):
    """Log everything that can make parts look hidden or ghosted, for diagnosis."""
    lines = []
    for comp in design.allComponents:
        count = comp.customGraphicsGroups.count
        if count:
            ids = [comp.customGraphicsGroups.item(i).id or "(no id)" for i in range(count)]
            lines.append("graphics groups in {}: {} {}".format(comp.name, count, ids))
    off = [o.fullPathName for o in design.rootComponent.allOccurrences if not o.isLightBulbOn]
    lines.append("occurrences with light bulb off: {} {}".format(len(off), off[:20]))
    faded = []
    for occ in design.rootComponent.allOccurrences:
        try:
            if occ.visibleOpacity < 0.999:
                faded.append("{}={:.2f}".format(occ.fullPathName, occ.visibleOpacity))
        except Exception:
            pass
    lines.append("occurrences drawn transparent: {} {}".format(len(faded), faded[:20]))
    comp_opacity = []
    for comp in design.allComponents:
        try:
            if comp.opacity < 0.999:
                comp_opacity.append("{}={:.2f}".format(comp.name, comp.opacity))
        except Exception:
            pass
    lines.append("components with opacity override: {} {}".format(len(comp_opacity), comp_opacity[:20]))
    for line in lines:
        log.info("diag: " + line)
    return lines


def _anchor_point(occ, local):
    """World position of a part's line start (component coordinates), or its box centre."""
    if not local:
        return refs.bbox_center(occ)
    p = adsk.core.Point3D.create(*local)
    p.transformBy(occ.transform2)
    return (p.x, p.y, p.z)


def segment_id(path, explode_id):
    return "{}{}{}".format(path, SEGMENT_SEP, explode_id)


def parse_segment_id(seg_id):
    """(part path, explode move id) of a trail segment."""
    path, _, explode_id = seg_id.rpartition(SEGMENT_SEP)
    return path, explode_id


def _entry(occ, moves):
    total = (0.0, 0.0, 0.0)
    for vec, _, _ in moves:
        total = explode.add(total, vec)
    return {"occ": occ, "moves": moves, "world": total}
