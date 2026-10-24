"""VM fixture: a slider from 0 to 1000 with the keyboard focus, in Qt 6, GTK 3 or Tk, that logs each value it takes.

    hyprmasq-slider-probe qt|gtk|tk TITLE

Its window is TITLE, 400×100, with the slider across it at 0. Qt and GTK run on Wayland, Tk on X11, which it takes from
DISPLAY. A press of Right moves the slider one step up in each. It logs to stdout, one event per line after a
CLOCK_MONOTONIC timestamp in milliseconds: "ready" once it has drawn, and "slider N" for each value the slider takes.
"""

import sys
import time


def say(*parts):
    print(int(time.monotonic() * 1000), *parts, flush=True)


def qt(title):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QSlider

    app = QApplication([sys.argv[0], "-platform", "wayland"])
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setWindowTitle(title)
    slider.setRange(0, 1000)
    slider.resize(400, 100)
    slider.valueChanged.connect(lambda value: say("slider", value))
    slider.show()
    slider.setFocus()
    QTimer.singleShot(0, lambda: say("ready"))
    sys.exit(app.exec())


def gtk(title):
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    window = Gtk.Window(title=title)
    window.set_default_size(400, 100)
    window.connect("destroy", Gtk.main_quit)
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1000, 1)
    scale.set_digits(0)
    scale.connect("value-changed", lambda s: say("slider", round(s.get_value())))
    window.add(scale)
    window.show_all()
    scale.grab_focus()
    GLib.idle_add(lambda: say("ready"))
    Gtk.main()


def tk(title):
    import tkinter

    root = tkinter.Tk()
    root.title(title)
    root.geometry("400x100")
    value = tkinter.IntVar(root, 0)
    # A trace of the variable sees every value, where the scale's command runs once for the values set before Tk idles.
    value.trace_add("write", lambda *_: say("slider", value.get()))
    scale = tkinter.Scale(root, from_=0, to=1000, resolution=1, orient="horizontal", variable=value)
    scale.pack(fill="both", expand=True)
    scale.focus_set()
    root.after_idle(lambda: say("ready"))
    root.mainloop()


{"qt": qt, "gtk": gtk, "tk": tk}[sys.argv[1]](sys.argv[2])
