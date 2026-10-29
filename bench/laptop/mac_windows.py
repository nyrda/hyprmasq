#!/usr/bin/env python3
"""Logs the task app's on-screen windows on a Mac: mac_windows.py APP USER-APP OUT, until it is terminated.

Every 0.2 s it reads the on-screen windows from Quartz, front to back, which needs no Screen Recording access, and
writes a line to OUT when APP's windows change: each window's layer and frame, and whether it is in front of
USER-APP's frontmost window and overlaps it, or above the normal windows' layer, as a dropdown's open list is, so
that the user sees it over their own work. A dropdown's list is a window of its own and shows that way even when its
app is behind the user's window.
"""

import json
import signal
import sys
import time

import Quartz

app, user_app, path = sys.argv[1:4]
running = True


def stop(*_):
    global running
    running = False


signal.signal(signal.SIGTERM, stop)


def frame(window):
    b = window.get("kCGWindowBounds") or {}
    return [int(b.get("X", 0)), int(b.get("Y", 0)), int(b.get("Width", 0)), int(b.get("Height", 0))]


def overlaps(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


with open(path, "a", buffering=1) as out:
    last = None
    while running:
        windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
        user = next((i for i, w in enumerate(windows)
                     if w.get("kCGWindowOwnerName") == user_app and int(w.get("kCGWindowLayer", 0)) == 0), None)
        user_frame = frame(windows[user]) if user is not None else None
        mine = []
        for i, w in enumerate(windows):
            if w.get("kCGWindowOwnerName") != app:
                continue
            f = frame(w)
            layer = int(w.get("kCGWindowLayer", 0))
            mine.append({"layer": layer, "frame": f,
                         "over_user": layer > 0 or (user is not None and i < user and overlaps(f, user_frame))})
        if mine != last:
            out.write(json.dumps({"time": time.time(), "windows": mine}) + "\n")
            last = mine
        time.sleep(0.2)
