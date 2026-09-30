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

import adsk.core
import adsk.fusion

from . import explode, log, model, refs
from .overlay import CROP_GROUP_ID

EDGE_TOLERANCE_CM = 0.01
EDGE_COLOR = (30, 30, 30)
PREVIEW_COLOR = (70, 130, 230)
PREVIEW_OPACITY = 0.55
GHOST_COLOR = (205, 205, 205)
HIGHLIGHT_COLOR = (0, 120, 215)     # checked parts, like Fusion's selection blue
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
        self._bodies = []           # (leaf path, mesh entity, its normal colour) of drawn copies
        self.highlighted = set()    # part paths shown highlighted (checked in the panel)

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

    def show(self, design, manual, step_id, edit=False, current=None, upto=None):
        """Render a step, running its explode moves in order.

        `upto` stops after that explode move (the panel's "state up to this
        move"). In `edit` mode (the explode command, which passes a working
        copy of the manual) moved copies are pickable (their graphics id is
        the occurrence path) and parts in `current` draw as a blue preview.
        """
        # The explode dialog (sticky) keeps the full reset: Fusion's preview
        # rollback can undo visibility behind our back there.
        self._reset(restore=self.sticky)
        _, step = model.find_step(manual, step_id)
        if step is None:
            self._restore_visibility()
            self.step_id = None
            return
        self.step_id = step_id
        self.edit = edit
        self.upto = upto if model.find_explode(step, upto) else None

        with log.timed("scene.show " + step["title"]):
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
        self._bodies = []
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
        for path, occ in index.items():
            if not self._user_visible(occ):
                continue
            ours = any(p in self._hidden_occs for p in [*_ancestors(path), path])
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

        # Root-component bodies count as unassigned.
        root_bodies = [b for b in design.rootComponent.bRepBodies
                       if b.isVisible or b.entityToken in self._hidden_bodies]

        # Trail line anchors must be read while everything is still at home.
        self.segments = self._trail_segments(current, anchors)

        hide_paths = {occ.fullPathName for occ, _, desired, _ in plans
                      if desired in ("exploded", PREVIEW, model.HIDDEN, model.GHOSTED)}

        self._group = design.rootComponent.customGraphicsGroups.add()
        self._group.id = GROUP_ID
        ghost = self._ghost_effect(settings)
        preview = adsk.fusion.CustomGraphicsBasicMaterialColorEffect.create(
            _color(PREVIEW_COLOR), _color(PREVIEW_COLOR), _color((255, 255, 255)),
            _color((0, 0, 0)), 30.0, PREVIEW_OPACITY)
        draw_edges = settings.get("drawEdges", True)

        drawn = 0
        for occ, bodies, desired, offset in plans:
            path = occ.fullPathName
            hidden_by_parent = any(a in hide_paths for a in _ancestors(path))
            if desired == "exploded":
                drawn += self._draw_bodies(occ, bodies, offset, SOLID, None, draw_edges)
            elif desired == PREVIEW:
                drawn += self._draw_bodies(occ, bodies, offset, PREVIEW, preview, False)
            elif desired == model.GHOSTED:
                drawn += self._draw_bodies(occ, bodies, offset, GHOST, ghost, False)
            elif desired == model.SHOWN and hidden_by_parent:
                drawn += self._draw_bodies(occ, bodies, offset, SOLID, None, draw_edges)

        hide_bodies = {}
        if unassigned_mode != model.SHOWN:
            for body in root_bodies:
                if unassigned_mode == model.GHOSTED:
                    drawn += self._draw_bodies(None, [body], (0.0, 0.0, 0.0), GHOST, ghost, False)
                hide_bodies[body.entityToken] = body

        self._draw_trails(settings)

        # Hide last, after every query that depends on visibility. Only the top
        # of each hidden branch needs its light bulb off.
        occ_by_path = {occ.fullPathName: occ for occ, _, _, _ in plans}
        hide_top = {p: occ_by_path[p] for p in hide_paths
                    if not any(a in hide_paths for a in _ancestors(p))}
        shown, hidden = self._apply_visibility(hide_top, hide_bodies)

        log.info("scene: {} occurrences, {} drawn bodies, {} hidden ({} turned on, {} turned off), {} trails".format(
            len(plans), drawn, len(self._hidden_occs), shown, hidden, len(self.segments)))

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

        In sticky mode (explode dialog) everything was already restored by the
        full reset and the record of touched parts must only grow, so this
        just hides and records.
        """
        shown = hidden = 0
        if self.sticky:
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

    def _draw_bodies(self, occ, bodies, offset, style, effect, draw_edges):
        matrix = self._placement(occ, offset)
        # Pickable copies (explode command) get a sub-group per part, id = its
        # path, so whatever Fusion reports for a click resolves to one part.
        pickable = self.edit and occ is not None and style in (SOLID, PREVIEW)
        target = self._group
        if pickable:
            target = self._group.addGroup()
            target.id = occ.fullPathName
            self._pickable.add(occ.fullPathName)
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
            entity.isSelectable = pickable
            if occ is not None:
                entity.id = occ.fullPathName
            entity.color = effect if effect is not None else self._appearance_effect(occ, body)
            if occ is not None and style != GHOST:
                self._bodies.append((occ.fullPathName, entity, entity.color))
            count += 1

            if draw_edges and style == SOLID:
                edges = self._edges(native)
                if edges and edges[1]:
                    lines = target.addLines(
                        adsk.fusion.CustomGraphicsCoordinates.create(edges[0]), [], True, edges[1])
                    lines.transform = matrix
                    lines.isSelectable = False
                    lines.weight = 1.0
                    lines.depthPriority = 1
                    lines.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(_color(EDGE_COLOR))
        return count

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
        for seg in self.segments.values():
            if seg["on"]:
                self._add_segment(seg, "on")

    def _add_segment(self, seg, state):
        coords = list(seg["start"]) + list(seg["end"])
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
        pattern = LINE_STYLES.get(trail.get("style", "dashed"), LINE_STYLES["dashed"])
        color = _rgb(trail.get("color", "#404040"))
        if state == "off":
            weight, pattern, color = 1.0, LINE_STYLES["dotted"], TRAIL_OFF_COLOR
        elif state == "hover":
            weight, color = weight + 2.5, TRAIL_HOVER_COLOR
        lines.weight = weight
        lines.lineStylePattern = pattern
        lines.lineStyleScale = float(trail.get("scale", 1.0))
        lines.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(_color(color))
        # While editing lines, shown lines are clickable; hidden ones only on request,
        # so overlapping lines resolve to a shown one.
        lines.isSelectable = self.lines_editing and (state != "off" or self.pick_hidden)
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
            else:
                entity.isSelectable = True

    def set_pick_hidden(self, flag):
        """Edit lines: make hidden (faint) lines clickable or not."""
        self.pick_hidden = bool(flag)
        for seg_id, entity in self._segment_entities.items():
            if entity.isValid and self._segment_states.get(seg_id) == "off":
                entity.isSelectable = self.lines_editing and self.pick_hidden

    def style_segment(self, seg_id, state):
        entity = self._segment_entities.get(seg_id)
        if entity is not None and entity.isValid:
            self._style(entity, state)


def sweep(design, everything=False, ids=(GROUP_ID, CROP_GROUP_ID, "BuildBookAnchor")):
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
