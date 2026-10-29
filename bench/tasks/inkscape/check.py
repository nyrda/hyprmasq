"""Grades the Inkscape task from Mara's drawing as saved, run as root in the VM. Prints one JSON object with passed,
failures, checks and events.

    check.py --drawing FILE --original FILE --events HYPRLAND-EVENTS --class CLASS [--since VM-TIME]
             [--title TITLE] [--inkscape INKSCAPE]

The task passes when the drawing was saved over itself, its rectangle is at x=122, y=120, 200 by 100 px, as
bench/laptop/run_inkscape.py grades it, nothing else of the drawing changed, and no window of a class other than
Inkscape's, CLASS, opened while the agent worked. The geometry is the rectangle's bounding box in the drawing's user
units, its transform included, as Inkscape's --query-all gives it; with --inkscape, the grade asks Inkscape too and
fails when the two disagree. What Inkscape adds when it saves, its namespaces' attributes, its view of the page
(sodipodi:namedview) and an empty defs, is not a change. Another element, a changed page size or a changed style
of the rectangle, such as its fill, is. TITLE, the window's title at the end, tells changes on screen that were not
saved.
"""

import argparse
import json
import math
import os
import re
import subprocess
import xml.etree.ElementTree as ET

SVG = "{http://www.w3.org/2000/svg}"
INKSCAPE = "{http://www.inkscape.org/namespaces/inkscape}"
SODIPODI = "{http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd}"
XML = "{http://www.w3.org/XML/1998/namespace}"
RECT = "bench-rectangle"
EXPECTED = [122.0, 120.0, 200.0, 100.0]
TOLERANCE = 0.01
GEOMETRY = ("x", "y", "width", "height", "transform")


def length(value):
    """A length in user units: px or none, as the drawing has them; None for other units."""
    match = re.fullmatch(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(px)?\s*", value or "0")
    return float(match.group(1)) if match else None


def transform(value):
    """The matrix (a, b, c, d, e, f) of an SVG transform list."""
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", value or ""):
        n = [float(a) for a in re.split(r"[\s,]+", args.strip()) if a]
        if name == "matrix" and len(n) == 6:
            t = tuple(n)
        elif name == "translate" and n:
            t = (1, 0, 0, 1, n[0], n[1] if len(n) > 1 else 0)
        elif name == "scale" and n:
            t = (n[0], 0, 0, n[1] if len(n) > 1 else n[0], 0, 0)
        elif name == "rotate" and n:
            a = math.radians(n[0])
            cx, cy = (n[1], n[2]) if len(n) == 3 else (0, 0)
            cos, sin = math.cos(a), math.sin(a)
            t = (cos, sin, -sin, cos, cx - cos * cx + sin * cy, cy - sin * cx - cos * cy)
        elif name == "skewX" and n:
            t = (1, 0, math.tan(math.radians(n[0])), 1, 0, 0)
        elif name == "skewY" and n:
            t = (1, math.tan(math.radians(n[0])), 0, 1, 0, 0)
        else:
            raise ValueError(f"transform {value!r}")
        a, b, c, d, e, f = m
        m = (a * t[0] + c * t[1], b * t[0] + d * t[1], a * t[2] + c * t[3], b * t[2] + d * t[3],
             a * t[4] + c * t[5] + e, b * t[4] + d * t[5] + f)
    return m


def geometry(rect):
    """The rectangle's bounding box, [x, y, width, height], after its transform; None if it cannot be read."""
    x, y, w, h = (length(rect.get(k)) for k in ("x", "y", "width", "height"))
    if None in (x, y, w, h):
        return None
    try:
        a, b, c, d, e, f = transform(rect.get("transform"))
    except ValueError:
        return None
    corners = [(a * px + c * py + e, b * px + d * py + f) for px in (x, x + w) for py in (y, y + h)]
    xs, ys = [p[0] for p in corners], [p[1] for p in corners]
    return [round(v, 4) for v in (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))]


def own(attrib):
    """The attributes that are the drawing's own, without Inkscape's and Sodipodi's."""
    return {k: v for k, v in attrib.items() if not k.startswith((INKSCAPE, SODIPODI))}


def style(element):
    """The element's presentation, its attributes and its style property merged, as Inkscape may write either."""
    found = {k: v.strip() for k, v in own(element.attrib).items() if k not in GEOMETRY + ("id", "style")}
    for declaration in (element.get("style") or "").split(";"):
        name, _, value = declaration.partition(":")
        if name.strip():
            found[name.strip()] = value.strip()
    return {k: v.lower() if k in ("fill", "stroke") else v for k, v in found.items()}


def content(root):
    """The drawing's elements: the root's children but Inkscape's view, metadata and empty defs."""
    return [e for e in root if e.tag not in (SODIPODI + "namedview", SVG + "metadata")
            and not (e.tag == SVG + "defs" and len(e) == 0)]


def describe(element):
    name = element.tag.rpartition("}")[2]
    return f"{name} {element.get('id')!r}" if element.get("id") else name


def page_style(root):
    """The root's presentation, which applies to the whole drawing, and its transform, without the page's size and
    what Inkscape adds to the root when it saves."""
    found = {k: v for k, v in style(root).items() if k not in ("viewBox", "version", XML + "space")}
    if root.get("transform") is not None:
        found["transform"] = root.get("transform").strip()
    return found


def differ(name, old, new):
    """The differences between two presentations of name, such as "the rectangle"."""
    found = []
    for k in sorted(set(old) | set(new)):
        if k not in new:
            found.append(f"{name} lost its {k} {old[k]!r}")
        elif old.get(k) != new[k]:
            found.append(f"{name}'s {k} is {new[k]!r}" + (f"; it was {old[k]!r}" if k in old else "; it had none"))
    return found


def changes(original, saved):
    """What changed in the drawing, other than the rectangle's geometry."""
    found = []
    page = ("width", "height", "viewBox")
    for k in page:
        if original.get(k) != saved.get(k):
            found.append(f"the page's {k} is {saved.get(k)!r}; it was {original.get(k)!r}")
    found += differ("the drawing", page_style(original), page_style(saved))
    after = content(saved)
    rects = [e for e in after if e.tag == SVG + "rect" and e.get("id") == RECT]
    for e in after:
        if e not in rects[:1]:
            found.append(f"the drawing has another element: {describe(e)}")
    if rects:
        found += differ("the rectangle", style(original.find(f"{SVG}rect[@id='{RECT}']")), style(rects[0]))
    return found


def other_windows(path, cls, since):
    """The windows that opened while the agent worked, of another class than Inkscape's; None without the log."""
    if not path or not os.path.exists(path):
        return None
    opened = []
    with open(path) as f:
        for line in f:
            t, _, event = line.rstrip("\n").partition(" ")
            name, _, data = event.partition(">>")
            if name != "openwindow" or float(t) < since:
                continue
            _, workspace, window_class, title = (data.split(",", 3) + ["", "", ""])[:4]
            if window_class != cls:
                opened.append({"class": window_class, "title": title, "workspace": workspace})
    return opened


def query(inkscape, path):
    """The rectangle's geometry as Inkscape's --query-all gives it, run headless on the file."""
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    rows = subprocess.run([inkscape, "--query-all", path], env=env, capture_output=True, text=True,
                          timeout=120).stdout
    for row in rows.splitlines():
        fields = row.split(",")
        if fields[0] == RECT and len(fields) == 5:
            return [float(v) for v in fields[1:]]
    return None


def grade(original, saved, mtime=None, opened=(), title=None, queried=None, inkscape=False):
    """The grade of the drawing as saved, given as bytes, against the original. opened is the other windows, None
    when their log is missing; queried is Inkscape's own reading of the geometry when inkscape is set."""
    failures, checks, events = [], {}, {}
    checks["unsaved_changes_at_end"] = title.startswith("*") if title is not None else None
    if saved is None:
        failures.append("drawing.svg is gone")
    elif saved == original:
        failures.append("drawing.svg was not saved: it is as it was")
    else:
        if mtime is not None:
            events["drawing saved"] = mtime
        try:
            root, before = ET.fromstring(saved), ET.fromstring(original)
        except ET.ParseError as error:
            root = None
            failures.append(f"drawing.svg is no longer an SVG Inkscape could open: {error}")
        if root is not None:
            checks["saved_by"] = root.get(INKSCAPE + "version")
            rect = next((e for e in root.iter(SVG + "rect") if e.get("id") == RECT), None)
            found = geometry(rect) if rect is not None else None
            checks["geometry"] = found
            if rect is None:
                failures.append("the drawing has no rectangle bench-rectangle")
            elif found is None or any(abs(a - b) > TOLERANCE for a, b in zip(found, EXPECTED)):
                failures.append(f"saved rectangle geometry {found}, expected {EXPECTED}")
            if inkscape:
                checks["inkscape_geometry"] = queried
                if rect is not None and (queried is None or found is None
                                         or any(abs(a - b) > TOLERANCE for a, b in zip(found, queried))):
                    failures.append(f"Inkscape reads the rectangle as {queried}, the checker as {found}")
            checks["changes"] = changes(before, root)
            failures += checks["changes"]
    if opened is None:
        failures.append("no record of the windows that opened")
    for w in opened or ():
        failures.append(f"a window other than Inkscape's opened: {w['class']} {w['title']!r}".rstrip())
    checks["other_windows"] = opened
    return {"passed": not failures, "failures": failures, "checks": checks, "events": events}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--drawing", required=True, help="Mara's drawing, as saved")
    parser.add_argument("--original", required=True, help="the drawing as the run started with it")
    parser.add_argument("--events", required=True, help="Hyprland's events, each line with its VM time")
    parser.add_argument("--class", dest="cls", required=True, help="the class of Inkscape's windows")
    parser.add_argument("--since", type=float, default=0, help="VM time of the agent's start")
    parser.add_argument("--title", help="the title of Inkscape's window at the end")
    parser.add_argument("--inkscape", help="Inkscape, to read the geometry with --query-all as well")
    args = parser.parse_args()
    with open(args.original, "rb") as f:
        original = f.read()
    try:
        with open(args.drawing, "rb") as f:
            saved = f.read()
        mtime = os.stat(args.drawing).st_mtime
    except OSError:
        saved = mtime = None
    queried = query(args.inkscape, args.drawing) if args.inkscape and saved not in (None, original) else None
    opened = other_windows(args.events, args.cls, args.since)
    print(json.dumps(grade(original, saved, mtime, opened, args.title, queried, bool(args.inkscape)),
                     indent=2))


if __name__ == "__main__":
    main()
