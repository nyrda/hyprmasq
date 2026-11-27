"""GTK 4 scrolling fixture with a fixed entry and a log of actual viewport positions.

With --spinner, a spinner turns beside the entry. With --second TITLE, the app has a second window. On SIGUSR1, a
notice appears between the entry and the page, which moves the page down, and the probe logs "notice"."""

import signal
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

options = sys.argv[2:]


def say(*parts):
    print(int(time.monotonic() * 1000), *parts, flush=True)


def activate(app):
    window = Gtk.ApplicationWindow(application=app, title=sys.argv[1])
    window.set_default_size(800, 600)
    layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    entry = Gtk.Entry()
    entry.set_size_request(-1, 48)
    entry.connect("changed", lambda e: say("entry", e.get_text()))
    if "--spinner" in options:
        header = Gtk.Box(spacing=8)
        entry.set_hexpand(True)
        header.append(entry)
        spinner = Gtk.Spinner(spinning=True, margin_end=8)
        spinner.set_size_request(32, 32)
        header.append(spinner)
        layout.append(header)
    else:
        layout.append(entry)
    notice = Gtk.Label(label="Notice: the records below were updated", xalign=0, visible=False)
    notice.set_size_request(-1, 40)
    layout.append(notice)

    def show_notice():
        notice.set_visible(True)
        say("notice")
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, show_notice)
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
    if "--second" in options:
        second = Gtk.ApplicationWindow(application=app, title=options[options.index("--second") + 1])
        second.set_default_size(200, 100)
        second.set_child(Gtk.Label(label="second"))
        second.present()


application = Gtk.Application(application_id="org.example.ScrollProbe", flags=Gio.ApplicationFlags.NON_UNIQUE)
application.connect("activate", activate)
application.run([sys.argv[0]])
