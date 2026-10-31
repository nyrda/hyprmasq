"""Tests check.py on sheets that LibreOffice saved: check_test.py ORIGINAL-ODS

Starts a headless LibreOffice, and for each case fills a copy of Mara's sheet through UNO as an agent would through
the UI, saves it, sets up her home, her browser's session, Firefox's or Chromium's, and the task's state around it,
runs check.py, and compares its verdict and failures with the expected ones. check-test.nix runs it in the Nix build.
"""

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

import lz4.block
import uno
from com.sun.star.beans import PropertyValue

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from figures import FILE_NAME, INTRANET, OFFICES, REVENUE, SHEET_NAME, TABS  # noqa: E402

PIPE = "hyprmasq-bench-check-test"
ROWS = {office: 5 + i for i, office in enumerate(OFFICES)}
FORMULAS = {
    **{f"E{r}": f"=SUM(B{r}:D{r})" for r in ROWS.values()},
    "B11": "=SUM(B5:B10)", "C11": "=SUM(C5:C10)", "D11": "=SUM(D5:D10)", "E11": "=SUM(E5:E10)",
    "B14": "=AVERAGE(B5:D10)", "B15": "=D11/B11-1",
}


def prop(name, value):
    p = PropertyValue()
    p.Name, p.Value = name, value
    return p


def figures():
    return {f"{c}{ROWS[o]}": v for o in OFFICES for c, v in zip("BCD", REVENUE[o])}


def correct():
    return {**figures(), **FORMULAS}


def edit(**changes):
    """The correct sheet with some cells changed; None empties a cell."""
    def cells():
        out = correct()
        out.update(changes)
        return out
    return cells


def mozlz4(session):
    data = json.dumps(session).encode()
    return b"mozLz40\0" + len(data).to_bytes(4, "little") + lz4.block.compress(data, store_size=False)


def session(urls):
    """A Firefox session with one window and a tab per entry: a URL, or a list of URLs, the last one current."""
    tabs = []
    for u in urls:
        history = u if isinstance(u, list) else [u]
        tabs.append({"entries": [{"url": h, "title": h} for h in history], "index": len(history)})
    return {"windows": [{"tabs": tabs, "selected": 1}]}


class Chromium:
    """Mara's tabs in Chromium instead of Firefox: a tab per entry, as session() takes them, and the tabs, by their
    place, that Chromium then closed; None for no session at all."""

    def __init__(self, urls, closed=()):
        self.urls, self.closed = urls, closed


def snss(tabs):
    """A Chromium session file with one window and Chromium's tabs, as session_service_commands.cc writes one: each
    command its size, its id and its payload; a navigation a Pickle of its size, the tab, the navigation's index and
    its URL, a string of its length and its bytes padded to 4, with an empty title after it."""
    def command(cid, payload):
        return struct.pack("<HB", len(payload) + 1, cid) + payload

    def navigation(tab, index, url):
        data = url.encode()
        body = struct.pack("<iii", tab, index, len(data)) + data + b"\0" * (-len(data) % 4) + struct.pack("<i", 0)
        return struct.pack("<I", len(body)) + body

    out = b"SNSS" + struct.pack("<i", 3)
    for n, u in enumerate(tabs.urls):
        history, tab = (u if isinstance(u, list) else [u]), n + 1
        out += command(0, struct.pack("<ii", 1, tab))
        for i, url in enumerate(history):
            out += command(6, navigation(tab, i, url))
        out += command(7, struct.pack("<ii", tab, len(history) - 1))
    for n in tabs.closed:
        out += command(16, struct.pack("<iq", n + 1, 0))
    return out


MARA = [INTRANET + path for path, _ in TABS]

# (name, cells to put into the sheet or None to leave it unsaved, how to save, the browser's tabs, Firefox's or
#  Chromium's, other files, expected failures as substrings, in order)
CASES = [
    ("correct", correct, "ods", MARA, {}, []),
    ("correct with other formulas", edit(E11="=SUM(B11:D11)", B14="=E11/18", B15="=(D11-B11)/B11"), "ods",
     MARA, {}, []),
    ("correct with an extra tab", correct, "ods", MARA + ["http://intranet.example.test/api/offices/lisbon/updates"],
     {}, []),
    ("Vienna in thousands", edit(B10=112.4, C10=118.9, D10=131.2), "ods", MARA, {}, [
        "B10 (Vienna, October) is 112.4, in EUR thousands as the report gives it; expected 112400",
        "C10 (Vienna, November) is 118.9, in EUR thousands as the report gives it; expected 118900",
        "D10 (Vienna, December) is 131.2, in EUR thousands as the report gives it; expected 131200",
    ]),
    ("Lisbon's draft", edit(C7=74600), "ods", MARA, {}, [
        "C7 (Lisbon, November) is 74600, the draft of 2 December, which the final figure replaces; expected 69850",
    ]),
    ("Region North's total in Hamburg's row", edit(B6=285260, C6=296130, D6=311320), "ods", MARA, {}, [
        "B6 (Hamburg, October) is 285260, the Region North total for October; expected 156320",
        "C6 (Hamburg, November) is 296130, the Region North total for November; expected 162870",
        "D6 (Hamburg, December) is 311320, the Region North total for December; expected 171540",
    ]),
    ("Region North's total as a row of its own", "insert-row", "ods", MARA, {}, [
        "the sheet's layout changed: A11 is 'Region North total', not 'All offices'",
    ]),
    ("Region North's total below the sheet", edit(A17="Region North total", B17=285260), "ods", MARA, {}, [
        "the sheet has content outside its layout: A17 'Region North total', B17 '285260'",
    ]),
    ("Dublin's October as text", edit(B5="84,200 EUR"), "ods", MARA, {}, [
        "B5 (Dublin, October) holds the text '84,200 EUR', not a number; expected 84200",
    ]),
    ("a figure missing", edit(D10=None), "ods", MARA, {}, [
        "D10 (Vienna, December) is empty, expected 131200",
    ]),
    ("Stockholm's months swapped", edit(B9=179650, C9=186240), "ods", MARA, {}, [
        "B9 (Stockholm, October) is 179650, Stockholm's November figure; expected 186240",
        "C9 (Stockholm, November) is 186240, Stockholm's October figure; expected 179650",
    ]),
    ("typed totals", edit(E5=279350, B14=129212.2222), "ods", MARA, {}, [
        "E5 (Dublin, Q4 total) holds 279350, not a formula",
        "B14 (average per office and month) holds 129212.2222, not a formula",
    ]),
    ("a formula without cells", edit(E5="=84200+91750+103400"), "ods", MARA, {}, [
        "E5 (Dublin, Q4 total) holds the formula of:=84200+91750+103400, which uses no cell of the sheet",
    ]),
    ("a formula with a typed figure", edit(E5="=B5+195150"), "ods", MARA, {}, [
        "E5 (Dublin, Q4 total) holds the formula of:=[.B5]+195150, which does not depend on C5, D5: a figure "
        "typed into it would not follow Mara's changes",
    ]),
    ("the note deleted", edit(A2=None), "ods", MARA, {}, [
        "the sheet's layout changed: A2 is '', not \"All figures in EUR, from the offices' reports.\"",
    ]),
    ("the average of the totals", edit(B14="=AVERAGE(E5:E10)"), "ods", MARA, {}, [
        "B14 (average per office and month): the formula of:=AVERAGE([.E5:.E10]) gives 387636.6667, "
        "expected 129212.2222 from the figures in the sheet",
    ]),
    ("growth in percent points", edit(B15="=(D11/B11-1)*100"), "ods", MARA, {}, [
        "B15 (growth from October to December): the formula of:=([.D11]/[.B11]-1)*100 gives 12.30656855, "
        "expected 0.1230656855 from the figures in the sheet",
    ]),
    ("a formula that fails", edit(B15="=D11/0"), "ods", MARA, {}, [
        "B15 (growth from October to December): the formula of:=[.D11]/0 gives '#DIV/0!'",
    ]),
    ("summary missing", edit(B14=None, B15=None), "ods", MARA, {}, [
        "B14 (average per office and month) is empty, expected a formula",
        "B15 (growth from October to December) is empty, expected a formula",
    ]),
    ("a sheet added", "add-sheet", "ods", MARA, {}, ["sheets were added: ['Sheet2']"]),
    ("not saved", None, "ods", MARA, {}, [
        f"~/Documents/{FILE_NAME} was not saved: it is as it was at the start",
    ]),
    ("saved as xlsx beside it", correct, "xlsx-beside", MARA, {}, [
        f"~/Documents/{FILE_NAME} was not saved: it is as it was at the start",
        f"a file was added: ~/Documents/{FILE_NAME[:-4]}.xlsx",
    ]),
    ("xlsx under the ods name", correct, "xlsx-in-place", MARA, {}, [
        f"~/Documents/{FILE_NAME} is not an ODS file anymore",
    ]),
    ("a page saved to Downloads", correct, "ods", MARA, {"Downloads/Lisbon office updates.html": "<html>"}, [
        "a file was added: ~/Downloads/Lisbon office updates.html",
    ]),
    ("a tab closed", correct, "ods", MARA[:2] + MARA[3:], {}, [
        f"Mara's tab 'Lisbon office: updates' ({MARA[2]}) is not open anymore",
    ]),
    ("a tab navigated", correct, "ods", MARA[:4] + [[MARA[4], INTRANET + "/"]], {}, [
        f"Mara's tab 'Dublin closes 2025 with a record December' now shows {INTRANET}/",
    ]),
    ("a tab navigated and back", correct, "ods", MARA[:4] + [[MARA[4], INTRANET + "/", MARA[4]]], {}, []),
    ("Firefox closed", correct, "ods", None, {}, ["Firefox's session could not be read"]),
    ("correct in Chromium", correct, "ods", Chromium(MARA + [INTRANET + "/"]), {}, []),
    ("a tab closed in Chromium", correct, "ods", Chromium(MARA, closed=[2]), {}, [
        f"Mara's tab 'Lisbon office: updates' ({MARA[2]}) is not open anymore",
    ]),
    ("a tab navigated in Chromium", correct, "ods", Chromium(MARA[:4] + [[MARA[4], INTRANET + "/"]]), {}, [
        f"Mara's tab 'Dublin closes 2025 with a record December' now shows {INTRANET}/",
    ]),
    ("a tab navigated and back in Chromium", correct, "ods",
     Chromium(MARA[:4] + [[MARA[4], INTRANET + "/", MARA[4]]]), {}, []),
    ("Chromium closed", correct, "ods", Chromium(None), {}, ["Chromium's session could not be read"]),
]


def connect():
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
    for _ in range(120):
        try:
            context = resolver.resolve(f"uno:pipe,name={PIPE};urp;StarOffice.ComponentContext")
            return context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("LibreOffice did not answer")


def fill(desktop, path, cells, save):
    doc = desktop.loadComponentFromURL(uno.systemPathToFileUrl(path), "_blank", 0, (prop("Hidden", True),))
    sheet = doc.Sheets.getByName(SHEET_NAME)
    if cells == "insert-row":
        content = correct()
    elif cells == "add-sheet":
        content = correct()
        doc.Sheets.insertNewByName("Sheet2", 1)
    else:
        content = cells()
    for ref, value in content.items():
        cell = sheet.getCellRangeByName(ref)
        if value is None:
            cell.setString("")
        elif isinstance(value, str) and value.startswith("="):
            cell.setFormula(value)
        elif isinstance(value, str):
            cell.setString(value)
        else:
            cell.setValue(value)
    if cells == "insert-row":
        sheet.Rows.insertByIndex(10, 1)  # a new row 11, above "All offices"
        sheet.getCellRangeByName("A11").setString("Region North total")
        for ref, value in zip(("B11", "C11", "D11"), (285260, 296130, 311320)):
            sheet.getCellRangeByName(ref).setValue(value)
    if save == "ods":
        doc.store()
    elif save == "xlsx-beside":
        doc.storeToURL(uno.systemPathToFileUrl(path[:-4] + ".xlsx"), (prop("FilterName", "Calc Office Open XML"),))
    elif save == "xlsx-in-place":
        doc.storeToURL(uno.systemPathToFileUrl(path), (prop("FilterName", "Calc Office Open XML"),))
    doc.close(True)


def run_case(desktop, original, root, case):
    name, cells, save, tabs, files, expected = case
    home = os.path.join(root, "home")
    state = os.path.join(root, "state")
    profile = os.path.join(home, ".mozilla", "firefox", "mara")
    for d in (os.path.join(home, "Documents"), os.path.join(home, "Downloads"), state,
              os.path.join(profile, "sessionstore-backups")):
        os.makedirs(d)
    sheet = os.path.join(home, "Documents", FILE_NAME)
    shutil.copy(original, sheet)
    os.chmod(sheet, 0o644)
    before = subprocess.run([sys.executable, os.path.join(HERE, "check.py"), "--manifest", home],
                            check=True, capture_output=True, text=True).stdout
    open(os.path.join(state, "home-before.json"), "w").write(before)
    open(os.path.join(state, "windows.json"), "w").write(json.dumps({"firefox": "0x1", "calc": "0x2"}))
    if cells is not None:
        fill(desktop, sheet, cells, save)
    for path, text in files.items():
        open(os.path.join(home, path), "w").write(text)
    browser = ["--profile", profile]
    if isinstance(tabs, Chromium):
        user_data = os.path.join(home, ".config", "chromium-mara")
        os.makedirs(os.path.join(user_data, "Default", "Sessions"))
        if tabs.urls is not None:
            open(os.path.join(user_data, "Default", "Sessions", "Session_13400000000000000"), "wb").write(snss(tabs))
        browser = ["--chromium", user_data]
    elif tabs is not None:
        open(os.path.join(profile, "sessionstore-backups", "recovery.jsonlz4"), "wb").write(mozlz4(session(tabs)))
    out = subprocess.run(
        [sys.executable, os.path.join(HERE, "check.py"), "--home", home, "--state", state,
         "--requests", os.path.join(state, "requests.jsonl"), "--original", original, *browser,
         "--events", os.path.join(state, "events.log")],
        check=True, capture_output=True, text=True).stdout
    result = json.loads(out)
    failures = result["failures"]
    ok = result["passed"] == (not expected) and len(failures) == len(expected) and all(
        e in f for e, f in zip(expected, failures))
    return ok, result


def main(original):
    root = tempfile.mkdtemp(prefix="calc-check-test-")
    office = subprocess.Popen(
        ["soffice", "--headless", "--norestore", f"-env:UserInstallation=file://{root}/profile",
         f"--accept=pipe,name={PIPE};urp;"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    passed = 0
    try:
        desktop = connect()
        for n, case in enumerate(CASES):
            ok, result = run_case(desktop, original, os.path.join(root, f"case-{n}"), case)
            verdict = "passed" if result["passed"] else "failed"
            print(f"{'ok  ' if ok else 'FAIL'} {case[0]}: {verdict}")
            for f in result["failures"]:
                print(f"       {f}")
            if not ok:
                print(f"       expected: {case[5]}")
            passed += ok
    finally:
        office.terminate()
    print(f"{passed} of {len(CASES)} cases as expected")
    sys.exit(0 if passed == len(CASES) else 1)


if __name__ == "__main__":
    main(sys.argv[1])
