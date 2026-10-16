"""Watches Mara's sheet while the agent works, for the task's milestones: watch.py PIPE FILE OUT

Calc runs with a UNO pipe that setup.py starts it with. Once a second this reads the cells of the open sheet
through it, without touching the document, and appends a line to OUT with the time whenever they changed:
{"time", "cells"} with each non-empty cell's formula or value as Calc shows it in the input line. It appends
{"time", "file"} whenever the file on disk changed, and {"time", "error"} while Calc does not answer.
"""

import json
import os
import sys
import time

import uno

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figures import SHEET_NAME  # noqa: E402

RANGE = "A1:H30"


def column(n):
    name = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        name = chr(65 + r) + name
    return name


def connect(pipe):
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
    context = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
    return context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)


def read(desktop, url):
    """The non-empty cells of the sheet in the document at url, or None when Calc does not have it open."""
    components = desktop.getComponents().createEnumeration()
    while components.hasMoreElements():
        doc = components.nextElement()
        if doc.supportsService("com.sun.star.sheet.SpreadsheetDocument") and doc.getURL() == url:
            sheets = doc.getSheets()
            sheet = sheets.getByName(SHEET_NAME) if sheets.hasByName(SHEET_NAME) else sheets.getByIndex(0)
            rows = sheet.getCellRangeByName(RANGE).getFormulaArray()
            return {
                f"{column(c)}{r + 1}": value
                for r, row in enumerate(rows)
                for c, value in enumerate(row)
                if value != ""
            }
    return None


def main(pipe, path, out):
    url = uno.systemPathToFileUrl(os.path.abspath(path))
    log = open(out, "a", buffering=1)
    desktop, last_cells, last_file, last_error = None, None, None, None
    while True:
        try:
            stat = os.stat(path)
            file = [stat.st_mtime_ns, stat.st_size]
        except OSError:
            file = None
        if file != last_file:
            log.write(json.dumps({"time": time.time(), "file": file}) + "\n")
            last_file = file
        try:
            desktop = desktop or connect(pipe)
            cells = read(desktop, url)
            error = None if cells is not None else "the sheet is not open"
        except Exception as e:  # Calc is starting, busy or gone: try again
            desktop, cells, error = None, None, f"{type(e).__name__}: {e}"
        if error != last_error:
            log.write(json.dumps({"time": time.time(), "error": error}) + "\n")
            last_error = error
        if cells is not None and cells != last_cells:
            log.write(json.dumps({"time": time.time(), "cells": cells}) + "\n")
            last_cells = cells
        time.sleep(1)


if __name__ == "__main__":
    main(*sys.argv[1:4])
