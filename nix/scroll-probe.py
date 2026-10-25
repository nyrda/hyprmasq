"""GTK 4 scrolling fixture with a fixed entry and a log of actual viewport positions."""

import sys
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk


def say(*parts):
    print(int(time.monotonic() * 1000), *parts, flush=True)


def activate(app):
    window = Gtk.ApplicationWindow(application=app, title=sys.argv[1])
    window.set_default_size(800, 600)
    layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    entry = Gtk.Entry()
    entry.set_size_request(-1, 48)
    entry.connect("changed", lambda e: say("entry", e.get_text()))
    layout.append(entry)
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    scroller.set_focusable(True)
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    for number in range(120):
        row = Gtk.Box(spacing=40, margin_start=24, margin_end=24)
        row.set_size_request(-1, 64)
        for text in [f"Record {number:03d}: {number * 7919 + 12345}",
                     f"Location {number * 101 + 37:05d}, shelf {number % 17}"]:
            row.append(Gtk.Label(label=text, xalign=0))
        rows.append(row)
    scroller.set_child(rows)
    adjustment = scroller.get_vadjustment()
    adjustment.connect("value-changed", lambda a: say("scroll", round(a.get_value()), round(a.get_page_size())))
    layout.append(scroller)
    window.set_child(layout)
    window.present()
    scroller.grab_focus()


application = Gtk.Application(application_id="org.example.ScrollProbe", flags=Gio.ApplicationFlags.NON_UNIQUE)
application.connect("activate", activate)
application.run([sys.argv[0]])
