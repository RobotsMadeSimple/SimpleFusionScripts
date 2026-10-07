"""Explode distances that follow Fusion's parameters.

A move's distance (and a stacked move's base distance, and the default explode distance) can be
an expression such as "bb_gap", "bb_gap * 2" or "rack_len + 10 mm": Fusion's user parameters
(Modify > Change Parameters). The expression is kept next to the number and worked out again every
time the manual is loaded, so changing a parameter updates every move that uses it. If it can't be
worked out (a parameter was deleted or renamed), the last number stays and the move is flagged.
"""

import re

from . import log

PLAIN = re.compile(r"^\s*[-+]?\s*(\d+(\.\d*)?|\.\d+)\s*(mm|cm|m|um|in|ft|\"|')?\s*$", re.IGNORECASE)


def is_expression(text):
    """True if `text` uses a parameter (anything beyond a plain number with an optional unit)."""
    return bool(text) and not PLAIN.match(text)


def evaluate(design, text):
    """cm for an expression, or None if Fusion can't work it out."""
    try:
        units = design.unitsManager
        if not units.isValidExpression(text, units.defaultLengthUnits):
            return None
        return units.evaluateExpression(text, units.defaultLengthUnits)
    except Exception:
        return None


def resolve(design, manual):
    """Work out every expression in the manual (in place). Moves whose expression is unreadable
    keep their last number and get "exprError": True. Returns how many expressions there were."""
    from . import model                 # (lazy: model imports nothing from here)
    if design is None:
        return 0
    count = 0
    settings = manual.get("settings", {})
    expr = settings.get("defaultDistanceExpr")
    if expr:
        count += 1
        value = evaluate(design, expr)
        if value is not None:
            settings["defaultDistance"] = abs(value)
    steps = [st for _, st in model.ordered_steps(manual)]
    overview = manual.get("overview", {}).get("step")
    if isinstance(overview, dict):
        steps.append(overview)
    for step in steps:
        for ex in step.get("explodes", []):
            ex.pop("exprError", None)
            expr = ex.get("distanceExpr")
            if expr:
                count += 1
                value = evaluate(design, expr)
                if value is None:
                    ex["exprError"] = True
                else:
                    # The expression's sign is the direction, like a typed negative distance.
                    ex["distance"] = abs(value)
                    if ex.get("direction", {}).get("kind") != model.DIR_XYZ:
                        ex["direction"] = model.signed_direction(ex["direction"], value < 0)
            expr = ex.get("baseExpr")
            if expr:
                count += 1
                value = evaluate(design, expr)
                if value is None:
                    ex["exprError"] = True
                else:
                    ex["base"] = abs(value)
    return count


def flip(text):
    """The other way: "-(expr)" <-> "expr"."""
    t = text.strip()
    m = re.match(r"^-\s*\((.*)\)$", t)
    return m.group(1).strip() if m else "-({})".format(t)

