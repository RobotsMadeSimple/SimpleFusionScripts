"""Per-component facts for the tree and the BOM.

Reading a component's part number / description / material can be very slow in
Fusion (hundreds of ms each on some designs, e.g. linked hardware), so:
- nothing slow is read while collecting: `basic` uses the name, plus facts
  already known;
- the slow facts are read by `read_details` in small batches in the background
  (the controller drives it with a custom event), and kept on disk
  (cache/components.json, keyed by design + component id + name) so later
  sessions start with them; Refresh (clear) forgets them;
- mass is only read on request ("Calculate masses"), kept in memory.
"""

import json
import os
import time

import adsk.fusion

from . import hardware, log

_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "cache", "components.json")


def _safe(read, default=""):
    try:
        value = read()
        return default if value is None else value
    except Exception:
        return default


class ComponentInfo:
    def __init__(self):
        self._disk = None
        self._dirty = False
        self.doc = ""
        self._mass, self._components = {}, {}
        self.timings = {"partNumber": 0.0, "description": 0.0, "material": 0.0, "count": 0}

    # ------------------------------------------------------------ cache

    def _cache(self):
        if self._disk is None:
            try:
                with open(_FILE, encoding="utf-8") as handle:
                    self._disk = json.load(handle)
            except Exception:
                self._disk = {}
        return self._disk

    def flush(self):
        if not self._dirty:
            return
        try:
            os.makedirs(os.path.dirname(_FILE), exist_ok=True)
            with open(_FILE, "w", encoding="utf-8") as handle:
                json.dump(self._cache(), handle)
            self._dirty = False
        except Exception:
            log.error("save component cache")

    def clear(self):
        """Forget everything (Refresh), on disk too for this design."""
        cache = self._cache()
        for key in [k for k in cache if k.startswith(self.doc + "|")]:
            del cache[key]
        self._dirty = True
        self.flush()
        self._mass, self._components = {}, {}

    def set_document(self, doc):
        if doc != self.doc:
            self.doc = doc
            self._mass, self._components = {}, {}

    @staticmethod
    def key(component):
        return _safe(lambda: component.id, "") or component.name

    def _disk_key(self, component, key=None):
        return "{}|{}|{}".format(self.doc, key or self.key(component), component.name)

    # ------------------------------------------------------------ reads

    def basic(self, component):
        """{"name", "hw"} without any slow read (remembers the component for read_details)."""
        key = self.key(component)
        self._components[key] = component
        name = component.name
        known = self._cache().get(self._disk_key(component, key))
        return {"name": name, "hw": known["hw"] if known else hardware.short_name(name)}

    def known(self, component):
        """The full facts if already read, else None."""
        return self._cache().get(self._disk_key(component))

    def full(self, component):
        """Facts for the BOM: what's known, blanks for what isn't read yet ("pending": True)."""
        key = self.key(component)
        facts = self.known(component)
        if facts is None:
            out = dict(self.basic(component), partNumber="", description="", material="", pending=True)
        else:
            out = dict(facts, name=component.name)
        out["mass"] = self._mass.get(key)
        return out

    def pending(self, keys=None):
        """Components whose details aren't read yet (all seen, or only `keys`)."""
        return [c for k, c in self._components.items()
                if (keys is None or k in keys) and self.known(c) is None and _safe(lambda: c.isValid, False)]

    def read_details(self, component):
        """The slow reads, timed; stored on disk (flush() writes)."""
        started = time.perf_counter()
        part_number = _safe(lambda: component.partNumber)
        t1 = time.perf_counter()
        description = _safe(lambda: component.description)
        t2 = time.perf_counter()
        material = self._material(component)
        t3 = time.perf_counter()
        self.timings["partNumber"] += t1 - started
        self.timings["description"] += t2 - t1
        self.timings["material"] += t3 - t2
        self.timings["count"] += 1
        name = component.name
        self._cache()[self._disk_key(component)] = {
            "hw": hardware.short_name(name, description, part_number),
            "partNumber": "" if part_number == name else part_number,   # Fusion defaults it to the name
            "description": description,
            "material": material,
        }
        self._dirty = True

    def log_timings(self):
        t = self.timings
        if t["count"]:
            log.info("component details: {} read; part number {:.0f} ms, description {:.0f} ms, material {:.0f} ms"
                     .format(t["count"], t["partNumber"] * 1000, t["description"] * 1000, t["material"] * 1000))
        self.timings = {"partNumber": 0.0, "description": 0.0, "material": 0.0, "count": 0}

    def calculate_mass(self, component):
        key = self.key(component)
        if key not in self._mass:
            props = component.getPhysicalProperties(adsk.fusion.CalculationAccuracy.LowCalculationAccuracy)
            self._mass[key] = props.mass
        return self._mass[key]

    def has_mass(self):
        return bool(self._mass)

    def components(self):
        """{key: component} for every component seen by basic()."""
        return dict(self._components)

    @staticmethod
    def _material(component):
        names = []
        for body in component.bRepBodies:
            name = _safe(lambda: body.material.name)
            if name and name not in names:
                names.append(name)
        if len(names) > 2:
            return "Mixed ({})".format(len(names))
        return ", ".join(names)
