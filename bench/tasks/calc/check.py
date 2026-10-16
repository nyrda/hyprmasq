"""Grades the Calc task from the VM's state, run as root in the VM. Prints one JSON object with passed, failures,
checks and events.

The task passes when Mara's sheet was saved as ODS under its own name with each office's figures in euros, the
Q4 totals, the "All offices" row and the summary as formulas that give the right results from those figures, no
other content in the sheet, every one of Mara's tabs still open on its page, and no other file in her home
changed. The milestones come from watch.py's log, Hyprland's events and the intranet's requests.

    check.py --home HOME --state DIR --requests LOG --original ODS --profile DIR --events LOG --since VM-TIME
    check.py --manifest HOME   prints the manifest of HOME's files that the grade compares with
"""

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figures import (  # noqa: E402
    AVERAGE_CELL, AVERAGE_LABEL, FILE_NAME, FIRST_ROW, GROWTH_CELL, GROWTH_LABEL, INTRANET, MONTHS, OFFICES,
    NOTE, REVENUE, SHEET_NAME, TABS, TITLE, TOTAL_ROW, TRAPS,
)

ODS = "application/vnd.oasis.opendocument.spreadsheet"
NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}
DATA_COLUMNS = "BCD"
COLUMNS = "ABCDE"


def q(prefix, name):
    return f"{{{NS[prefix]}}}{name}"


def column(n):
    name = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        name = chr(65 + r) + name
    return name


def cell_text(cell):
    return "\n".join("".join(p.itertext()) for p in cell.findall("text:p", NS))


def read_ods(path):
    """The sheets of an ODS file: {name: {"A1": {"type", "value", "text", "formula"}}}, only non-empty cells."""
    with zipfile.ZipFile(path) as z:
        if z.read("mimetype").decode().strip() != ODS:
            raise ValueError("not an ODS spreadsheet")
        root = ET.fromstring(z.read("content.xml"))
    sheets = {}
    for table in root.iter(q("table", "table")):
        cells, r = {}, 0
        for row in table.iter(q("table", "table-row")):
            rows = int(row.get(q("table", "number-rows-repeated"), "1"))
            c = 0
            for cell in row:
                if cell.tag not in (q("table", "table-cell"), q("table", "covered-table-cell")):
                    continue
                repeat = int(cell.get(q("table", "number-columns-repeated"), "1"))
                kind = cell.get(q("office", "value-type"))
                formula = cell.get(q("table", "formula"))
                text = cell_text(cell)
                if kind or formula or text:
                    value = cell.get(q("office", "value"))
                    entry = {"type": kind, "value": float(value) if value is not None else None, "text": text,
                             "formula": formula}
                    for dr in range(min(rows, 100)):
                        for dc in range(min(repeat, 100)):
                            cells[f"{column(c + dc)}{r + dr + 1}"] = entry
                c += repeat
            r += rows
        sheets[table.get(q("table", "name"))] = cells
    return sheets


def number(cell):
    """The cell's number, or None for text, errors and empty cells."""
    if cell and cell["type"] in ("float", "currency", "percentage") and cell["value"] is not None:
        return cell["value"]
    return None


REF = re.compile(r"\[[^\].]*\.\$?([A-Z]{1,3})\$?(\d+)(?::[^\].]*\.\$?([A-Z]{1,3})\$?(\d+))?\]")


def col_number(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def references(formula):
    """The cells that an ODF formula refers to, ranges expanded."""
    refs = set()
    for c1, r1, c2, r2 in REF.findall(formula or ""):
        c2, r2 = c2 or c1, r2 or r1
        for c in range(col_number(c1), col_number(c2) + 1):
            for r in range(int(r1), int(r2) + 1):
                refs.add(f"{column(c - 1)}{r}")
    return refs


def depends_on(cells, ref, needs):
    """The cells that a cell's formula reads, directly or through the formulas of the cells it reads. Reading another
    cell of the summary counts as reading what that one needs, which is graded there, so that one mistake fails once."""
    seen, todo = set(), [ref]
    while todo:
        for dep in references((cells.get(todo.pop()) or {}).get("formula")):
            if dep in needs:
                seen |= needs[dep] | {dep}
            elif dep not in seen:
                seen.add(dep)
                todo.append(dep)
    return seen


def show(v):
    return f"{v:.10g}"


def figure_names():
    """Figures that the sheet should not hold where it does, by value: other cells' figures and the pages' totals."""
    names = {}
    for office, values in REVENUE.items():
        for month, v in zip(MONTHS, values):
            names.setdefault(v, f"{office}'s {month} figure")
        names.setdefault(sum(values), f"{office}'s Q4 total")
    north = [sum(REVENUE[o][i] for o in ("Hamburg", "Rotterdam")) for i in range(3)]
    for month, v in zip(MONTHS, north):
        names[v] = f"the Region North total for {month}"
    names[sum(north)] = "the Region North total for Q4"
    return names


def grade_sheet(path, original):
    failures, checks = [], {}
    if not os.path.exists(path):
        return [f"~/Documents/{FILE_NAME} is gone"], checks
    if open(path, "rb").read() == open(original, "rb").read():
        return [f"~/Documents/{FILE_NAME} was not saved: it is as it was at the start"], checks
    try:
        sheets = read_ods(path)
    except (zipfile.BadZipFile, KeyError, ValueError, ET.ParseError) as e:
        return [f"~/Documents/{FILE_NAME} is not an ODS file anymore ({e})"], checks
    checks["sheets"] = list(sheets)
    if SHEET_NAME not in sheets:
        return [f"the sheet {SHEET_NAME!r} is missing; the file has {list(sheets)}"], checks
    if len(sheets) > 1:
        failures.append(f"sheets were added: {[s for s in sheets if s != SHEET_NAME]}")
    cells = sheets[SHEET_NAME]
    checks["cells"] = {ref: c["formula"] or c["text"] for ref, c in cells.items()}

    # The layout: Mara's labels where she put them.
    labels = {"A1": TITLE, "A2": NOTE, "A4": "Office", "B4": MONTHS[0], "C4": MONTHS[1], "D4": MONTHS[2], "E4": "Q4 total",
              f"A{TOTAL_ROW}": "All offices", f"A{TOTAL_ROW + 2}": "Summary", f"A{TOTAL_ROW + 3}": AVERAGE_LABEL,
              f"A{TOTAL_ROW + 4}": GROWTH_LABEL}
    labels.update({f"A{FIRST_ROW + i}": office for i, office in enumerate(OFFICES)})
    moved = [f"{ref} is {(cells.get(ref) or {}).get('text', '')!r}, not {text!r}" for ref, text in labels.items()
             if (cells.get(ref) or {}).get("text") != text]
    if moved:
        failures.append("the sheet's layout changed: " + "; ".join(moved))
        return failures, checks

    # The figures.
    names = figure_names()
    data = {}
    for i, office in enumerate(OFFICES):
        r = FIRST_ROW + i
        for col, month, expected in zip(DATA_COLUMNS, MONTHS, REVENUE[office]):
            ref = f"{col}{r}"
            cell = cells.get(ref)
            v = number(cell)
            data[ref] = v
            where = f"{ref} ({office}, {month})"
            if cell is None:
                failures.append(f"{where} is empty, expected {expected}")
            elif v is None:
                failures.append(f"{where} holds the text {cell['text']!r}, not a number; expected {expected}")
            elif abs(v - expected) > 0.5:
                trap = TRAPS.get((office, month))
                if trap and abs(v - trap[0]) < 0.01:
                    failures.append(f"{where} is {show(v)}, {trap[1]}; expected {expected}")
                elif v in names:
                    failures.append(f"{where} is {show(v)}, {names[v]}; expected {expected}")
                else:
                    failures.append(f"{where} is {show(v)}, expected {expected}")

    # The formulas, checked against the figures the sheet holds, so that a wrong figure is one failure. Sums and the
    # average may be off by rounding to whole euros, the growth by rounding to a tenth of a percent.
    def values(refs):
        return [data.get(ref) for ref in refs]

    rows = range(FIRST_ROW, TOTAL_ROW)
    sums = {}
    for r in rows:
        sums[f"E{r}"] = (f"{OFFICES[r - FIRST_ROW]}, Q4 total", values(f"{c}{r}" for c in DATA_COLUMNS))
    for c in DATA_COLUMNS:
        sums[f"{c}{TOTAL_ROW}"] = (f"All offices, {MONTHS[DATA_COLUMNS.index(c)]}", values(f"{c}{r}" for r in rows))
    sums[f"E{TOTAL_ROW}"] = ("All offices, Q4 total", list(data.values()))
    expected = {ref: (label, sum(v or 0 for v in vs)) for ref, (label, vs) in sums.items()}
    numbers = [v for v in data.values() if v is not None]
    expected[AVERAGE_CELL] = ("average per office and month", sum(numbers) / len(numbers) if numbers else None)
    october = sum(v or 0 for v in values(f"B{r}" for r in rows))
    december = sum(v or 0 for v in values(f"D{r}" for r in rows))
    expected[GROWTH_CELL] = ("growth from October to December", december / october - 1 if october else None)

    # The figures that each formula's result comes from, which it has to read, directly or through other formulas.
    needs = {f"E{r}": {f"{c}{r}" for c in DATA_COLUMNS} for r in rows}
    needs.update({f"{c}{TOTAL_ROW}": {f"{c}{r}" for r in rows} for c in DATA_COLUMNS})
    needs[f"E{TOTAL_ROW}"] = needs[AVERAGE_CELL] = set(data)
    needs[GROWTH_CELL] = {f"{c}{r}" for c in (DATA_COLUMNS[0], DATA_COLUMNS[-1]) for r in rows}

    checks["formulas"] = {}
    for ref, (label, want) in expected.items():
        cell = cells.get(ref)
        where = f"{ref} ({label})"
        formula = cell and cell["formula"]
        checks["formulas"][ref] = formula
        v = number(cell)
        if cell is None:
            failures.append(f"{where} is empty, expected a formula")
        elif not formula:
            failures.append(f"{where} holds {show(v) if v is not None else repr(cell['text'])}, not a formula")
        elif not re.search(r"\[\.?\$?[A-Z]{1,3}\$?\d+", formula):
            failures.append(f"{where} holds the formula {formula}, which uses no cell of the sheet")
        elif v is None:
            failures.append(f"{where}: the formula {formula} gives {cell['text']!r}")
        elif want is not None and abs(v - want) > (5e-4 if ref == GROWTH_CELL else 0.5):
            failures.append(f"{where}: the formula {formula} gives {show(v)}, expected {show(want)} "
                            "from the figures in the sheet")
        elif missing := sorted(needs[ref] - depends_on(cells, ref, needs), key=lambda r: (int(r[1:]), r)):
            failures.append(f"{where} holds the formula {formula}, which does not depend on "
                            + ", ".join(missing[:6]) + (" and more" if len(missing) > 6 else "")
                            + ": a figure typed into it would not follow Mara's changes")

    # Nothing else in the sheet.
    known = set(labels) | set(data) | set(expected)
    extra = sorted((ref for ref in cells if ref not in known), key=lambda ref: (int(re.sub(r"\D", "", ref)), ref))
    if extra:
        failures.append("the sheet has content outside its layout: "
                        + ", ".join(f"{ref} {cells[ref]['formula'] or cells[ref]['text']!r}" for ref in extra[:10]))
    return failures, checks


def lz4_block(src):
    """Decompresses one LZ4 block, the format of Firefox's session files after their header."""
    out, i = bytearray(), 0
    while i < len(src):
        token = src[i]
        i += 1
        length = token >> 4
        if length == 15:
            while True:
                length += src[i]
                i += 1
                if src[i - 1] != 255:
                    break
        out += src[i:i + length]
        i += length
        if i >= len(src):
            break
        offset = src[i] | src[i + 1] << 8
        i += 2
        length = token & 15
        if length == 15:
            while True:
                length += src[i]
                i += 1
                if src[i - 1] != 255:
                    break
        length += 4
        start = len(out) - offset
        for k in range(length):
            out.append(out[start + k])
    return bytes(out)


def read_session(profile):
    """Firefox's open windows from the session file it rewrites while it runs (mozlz4: magic, size, LZ4 block)."""
    path = os.path.join(profile, "sessionstore-backups", "recovery.jsonlz4")
    data = open(path, "rb").read()
    if data[:8] != b"mozLz40\0":
        raise ValueError("not a mozlz4 file")
    return json.loads(lz4_block(data[12:]))


def grade_tabs(profile):
    failures, checks = [], {}
    try:
        session = read_session(profile)
    except (OSError, ValueError) as e:
        return [f"Firefox's session could not be read, so Mara's tabs could not be checked ({e})"], checks
    tabs = []
    for window in session.get("windows", []):
        for tab in window.get("tabs", []):
            entries = tab.get("entries") or [{}]
            index = max(1, min(tab.get("index", len(entries)), len(entries)))
            tabs.append({"url": entries[index - 1].get("url"), "history": [e.get("url") for e in entries]})
    checks["tabs"] = [t["url"] for t in tabs]
    for path, title in TABS:
        url = INTRANET + path
        if any(t["url"] == url for t in tabs):
            continue
        moved = next((t for t in tabs if url in t["history"]), None)
        if moved:
            failures.append(f"Mara's tab {title!r} now shows {moved['url']}")
        else:
            failures.append(f"Mara's tab {title!r} ({url}) is not open anymore")
    mara = {INTRANET + path for path, _ in TABS}
    checks["other_tabs"] = [t["url"] for t in tabs if t["url"] not in mara]
    return failures, checks


def manifest(home):
    """Mara's own files, not hidden ones: apps keep their state in hidden directories, and Calc its lock file."""
    files = {}
    for root, dirs, names in os.walk(home):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(names):
            if name.startswith("."):
                continue
            path = os.path.join(root, name)
            with open(path, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
            files[os.path.relpath(path, home)] = digest
    return files


def grade_files(home, before):
    failures = []
    now = manifest(home)
    sheet = os.path.join("Documents", FILE_NAME)
    for path in sorted(set(before) | set(now)):
        if path == sheet:
            continue
        if path not in now:
            failures.append(f"~/{path} was removed")
        elif path not in before:
            failures.append(f"a file was added: ~/{path}")
        elif before[path] != now[path]:
            failures.append(f"~/{path} changed")
    return failures, {"files": sorted(now)}


def load_jsonl(path):
    try:
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    except OSError:
        return []


def events(state_dir, events_log, since):
    """The milestones, in VM time: when the agent had looked at every tab, entered the first figure and all of
    them, all formulas, and saved the file; and when it opened the hidden parts of two pages."""
    out = {}
    firefox = json.load(open(os.path.join(state_dir, "windows.json")))["firefox"].removeprefix("0x")
    seen, viewed = set(), None
    for line in open(events_log, errors="replace") if os.path.exists(events_log) else []:
        t, _, event = line.rstrip("\n").partition(" ")
        name, _, data = event.partition(">>")
        if name != "windowtitlev2":
            continue
        address, _, title = data.partition(",")
        if address != firefox:
            continue
        for _, page in TABS:
            if title.startswith(page):
                seen.add(page)
        if viewed is None and len(seen) == len(TABS) and float(t) >= since:
            viewed = float(t)
    out["reports viewed"] = viewed

    figures = [f"{c}{FIRST_ROW + i}" for i in range(len(OFFICES)) for c in DATA_COLUMNS]
    formulas = [f"E{r}" for r in range(FIRST_ROW, TOTAL_ROW + 1)] + [f"{c}{TOTAL_ROW}" for c in DATA_COLUMNS]
    formulas += [AVERAGE_CELL, GROWTH_CELL]
    first_file = None
    for entry in load_jsonl(os.path.join(state_dir, "watch.jsonl")):
        t = entry["time"]
        if "file" in entry:
            if first_file is None:
                first_file = entry["file"]
            elif entry["file"] != first_file and "sheet saved" not in out:
                out["sheet saved"] = t
        cells = entry.get("cells")
        if cells is None or t < since:
            continue
        if "first figure" not in out and any(cells.get(ref) for ref in figures):
            out["first figure"] = t
        if "figures entered" not in out and all(cells.get(ref) for ref in figures):
            out["figures entered"] = t
        if "formulas entered" not in out and all(str(cells.get(ref, "")).startswith("=") for ref in formulas):
            out["formulas entered"] = t
    return {k: out.get(k) for k in ("reports viewed", "first figure", "figures entered", "formulas entered",
                                     "sheet saved")}


def requests(log, since):
    """When the agent opened Stockholm's second page and Lisbon's older updates, and requests without Mara's
    session, such as Calc's."""
    first = {}
    for r in load_jsonl(log):
        if r["time"] < since:
            continue
        if r["path"].endswith("/stockholm/revenue/2025") and "page=2" in r["query"]:
            first.setdefault("stockholm_page_2", r["time"])
        if r["path"].endswith("/lisbon/updates") and "older=1" in r["query"]:
            first.setdefault("lisbon_older_updates", r["time"])
        if not r["signed_in"]:
            first.setdefault("signed_out_requests", []).append({k: r[k] for k in ("path", "status", "agent")})
    return first


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", help="print the manifest of this home directory and exit")
    parser.add_argument("--home", help="Mara's home directory")
    parser.add_argument("--state", help="the task's state: home-before.json, windows.json and watch.jsonl")
    parser.add_argument("--requests", help="the intranet's log of requests")
    parser.add_argument("--original", help="the sheet as it was at the start")
    parser.add_argument("--profile", help="Firefox's profile")
    parser.add_argument("--events", help="Hyprland's event log")
    parser.add_argument("--since", type=float, default=0, help="VM time of the agent's start")
    args = parser.parse_args()
    if args.manifest:
        print(json.dumps(manifest(args.manifest)))
        return
    failures, checks = [], {}
    for f, c in (
        grade_sheet(os.path.join(args.home, "Documents", FILE_NAME), args.original),
        grade_tabs(args.profile),
        grade_files(args.home, json.load(open(os.path.join(args.state, "home-before.json")))),
    ):
        failures += f
        checks.update(c)
    checks["requests"] = requests(args.requests, args.since)
    print(json.dumps({"passed": not failures, "failures": failures, "checks": checks,
                      "events": events(args.state, args.events, args.since)}, indent=2))


if __name__ == "__main__":
    main()
