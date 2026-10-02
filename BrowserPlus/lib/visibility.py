"""Temporary hiding (mate view, isolate a folder) that puts back exactly what it changed."""

from . import log


class Hider:
    def __init__(self):
        self.hidden = []            # occurrences / bodies whose light bulb we switched off

    @property
    def active(self):
        return bool(self.hidden)

    def switch_off(self, item):
        if item.isLightBulbOn:
            item.isLightBulbOn = False
            self.hidden.append(item)

    def hide_others(self, design, keep):
        """Hide everything but the parts in `keep` (paths), switching as few bulbs as possible:
        a subassembly with nothing to keep goes off as a whole."""
        def walk(occurrences):
            for occ in occurrences:
                path = occ.fullPathName
                if path in keep:
                    continue
                if any(k.startswith(path + "+") for k in keep):
                    walk(occ.childOccurrences)
                    for body in occ.bRepBodies:
                        self.switch_off(body)
                else:
                    self.switch_off(occ)
        walk(design.rootComponent.occurrences)
        for body in design.rootComponent.bRepBodies:
            self.switch_off(body)

    def paths(self):
        """Paths of the parts (occurrences) this has switched off."""
        out = set()
        for item in self.hidden:
            try:
                if item.isValid and item.objectType == "adsk::fusion::Occurrence":
                    out.add(item.fullPathName)
            except Exception:
                pass
        return out

    def restore(self):
        for item in reversed(self.hidden):
            try:
                if item.isValid:
                    item.isLightBulbOn = True
            except Exception:
                log.error("restore visibility")
        self.hidden = []
