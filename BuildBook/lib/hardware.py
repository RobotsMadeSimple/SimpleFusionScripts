"""Short labels for hardware: "ISO 4762 Socket Head Cap Screw M3 x 12" -> "M3x12 SHCS".

Pure Python (tested in tests/test_core.py). `short_name` looks through the
component name, then its description, then its part number, for a metric
(M3, M3x12, M3-0.5 x 12) or imperial (#4-40 x 1/2, 1/4-20 x 1-1/2) size, and
adds a kind when one is named (SHCS/BHCS/FHCS/set screw, nut, washer, insert,
standoff...). Returns None when nothing looks like hardware, so ordinary part
names are left alone.
"""

import re

# Kind words, most specific first: (pattern, label, is_screw_head)
_KINDS = [
    (r"lock\s*nut|nylock|nyloc", "lock nut", False),
    (r"t[-\s]?nut|tee\s*nut|hammer\s*nut|drop[-\s]?in\s*nut", "T-nut", False),
    (r"\bnut\b", "nut", False),
    (r"lock\s*washer|split\s*washer|spring\s*washer", "lock washer", False),
    (r"washer", "washer", False),
    (r"heat[-\s]?set|threaded\s*insert|\binsert\b", "insert", False),
    (r"standoff", "standoff", False),
    (r"spacer", "spacer", False),
    (r"set\s*screw|grub", "set screw", True),
    (r"dowel|\bpin\b", "pin", False),
    (r"button\s*head|\bbhcs\b|iso\s*7380", "BHCS", True),
    (r"flat\s*head|countersunk|\bfhcs\b|iso\s*10642|din\s*7991", "FHCS", True),
    (r"socket\s*head|\bshcs\b|iso\s*4762|din\s*912", "SHCS", True),
    (r"hex\s*head|\bhhcs\b|iso\s*4017|iso\s*4014|din\s*933", "hex bolt", True),
    (r"pan\s*head", "pan head", True),
]

_NUM = r"\d+(?:\.\d+)?"
# M3, M3x12, M3 x 12, M3-0.5x12, M3 x 0.5 x 12, M3x12mm
_METRIC = re.compile(
    r"(?<![A-Za-z0-9])M(" + _NUM + r")"
    r"(?:\s*[-xX×]\s*(" + _NUM + r"))?"
    r"(?:\s*[xX×]\s*(" + _NUM + r"))?"
    r"(?:\s*mm)?")
_LENGTH = re.compile(r"(" + _NUM + r")\s*mm\s+long", re.IGNORECASE)
# #4-40 x 1/2, 1/4-20 x 1-1/2, 1/4"-20 x 1", 10-32 x 3/8
_FRACTION = r"\d+-\d+/\d+|\d+/\d+|\d+(?:\.\d+)?"
_IMPERIAL = re.compile(
    r"(#\d+|\d+/\d+|\d+(?:\.\d+)?)\s*\"?\s*-\s*(\d+)"
    r"(?:\s*(?:UNC|UNF))?"
    r"(?:\s*[xX×]\s*(" + _FRACTION + r")\s*\"?)?")


def _kind(text):
    low = text.lower()
    for pattern, label, _ in _KINDS:
        if re.search(pattern, low):
            return label
    return None


def _metric(text):
    m = _METRIC.search(text)
    if not m:
        return None
    size, second, third = m.group(1), m.group(2), m.group(3)
    if float(size) <= 0 or float(size) > 64:
        return None
    length = None
    if third:
        length = third                 # M3 x 0.5 x 12 / M3-0.5 x 12: second is the pitch
    elif second:
        # A small decimal right after the size is a thread pitch (M3-0.5, M3 x 0.5 mm
        # Thread), never a length; anything else (M3x12, M3 x 12) is the length.
        is_pitch = "." in second and float(second) < 3
        length = None if is_pitch else second
    if length is None:
        long_ = _LENGTH.search(text)   # McMaster style: "..., 12 mm Long"
        if long_:
            length = long_.group(1)
    return "M{}".format(_trim(size)) + ("x{}".format(_trim(length)) if length else "")


def _imperial(text):
    m = _IMPERIAL.search(text)
    if not m:
        return None
    size, tpi, length = m.group(1), m.group(2), m.group(3)
    if not (size.startswith("#") or "/" in size):
        # Bare "10-32" is fine, but plain numbers like "2020-3" (extrusions) aren't threads.
        if not (size.isdigit() and int(size) <= 12 and 4 <= int(tpi) <= 80):
            return None
    return "{}-{}".format(size, tpi) + ("x{}".format(length) if length else "")


def _trim(num):
    return num[:-2] if num.endswith(".0") else num


def short_name(*texts):
    """Short hardware label from the first text (name, description, part number) with a size."""
    texts = [t for t in texts if t]
    size = None
    for text in texts:
        size = _metric(text) or _imperial(text)
        if size:
            break
    if not size:
        return None
    kind = None
    for text in texts:
        kind = _kind(text)
        if kind:
            break
    if kind is None:
        # A bare size with no length and no kind (e.g. "M3 rod end") isn't clearly hardware.
        return size if "x" in size else None
    return "{} {}".format(size, kind)


def natural_key(text):
    """Sort key that orders numbers by value: "M3x8" before "M3x12", "Part 2" before "Part 10"."""
    return [(0, float(tok), "") if tok[0].isdigit() else (1, 0.0, tok.lower())
            for tok in re.findall(r"\d+(?:\.\d+)?|\D+", text or "")]


# A catalogue-style part number (McMaster 91290A113, 94180A331...) or a
# hardware word: worth reading the component's description / part number.
_CATALOGUE = re.compile(r"^\s*\d{4,5}[A-Z]\d{2,4}\b")
_HARDWARE_WORD = re.compile(r"screw|bolt|nut|washer|insert|standoff|spacer|dowel|\bpin\b|fastener|shcs|bhcs|fhcs",
                            re.IGNORECASE)


def needs_details(name):
    """True if a component's (slow to read) description and part number could add a size.

    Reading those properties can take a noticeable time per component, so
    they're only read when the name alone has no size but looks like hardware.
    """
    if not name or short_name(name):
        return False
    return bool(_CATALOGUE.search(name) or _HARDWARE_WORD.search(name))
