"""VM fixture: a GTK 3 window with a combo box, run with GDK_BACKEND=x11, whose choices open in a menu: a window of its
own, of the override-redirect kind, that grabs the pointer and keyboard as GTK 3's menus do.

    hyprmasq-combo-probe TITLE

The window is TITLE, 300x400, with the combo box at 20,200 and Zero chosen out of Zero to Five, so that the menu has
room above and below it for every choice, which GTK puts over the combo box. It logs to stdout, one event per line
after a CLOCK_MONOTONIC timestamp in milliseconds: "popup shown" and "popup hidden" as the menu opens and closes,
"menu CHOICE X Y W H" for each item once the menu has its place, in the menu window's coordinates, and "changed CHOICE"
when the choice changes.

    hyprmasq-combo-probe --scroll TITLE

The window is TITLE, 300x400, with a scrolled column of 200 rows, and logs "scroll V" with the column's vertical
position in pixels whenever it changes, as a wheel or a kinetic scroll moves it.
"""

import sys
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

CHOICES = ["Zero", "One", "Two", "Three", "Four", "Five"]


def say(*parts):
    print(int(time.monotonic() * 1000), *parts, flush=True)


def menu_of(combo):
    for toplevel in Gtk.Window.list_toplevels():
        child = toplevel.get_child()
        if isinstance(child, Gtk.Menu) and child.get_attach_widget() == combo:
            return child
    return None


def report_items(combo):
    menu = menu_of(combo)
    if not menu or not menu.get_mapped():
        return True
    toplevel = menu.get_toplevel()
    # The items are the choices in order.
    for choice, item in zip(CHOICES, menu.get_children()):
        x, y = item.translate_coordinates(toplevel, 0, 0)
        allocation = item.get_allocation()
        say("menu", choice, x, y, allocation.width, allocation.height)
    return False


def popup_shown(combo, _):
    if combo.props.popup_shown:
        say("popup shown")
        # The menu has its place once it is mapped.
        GLib.timeout_add(50, report_items, combo)
    else:
        say("popup hidden")


def scroll_probe(title):
    window = Gtk.Window(title=title)
    window.set_default_size(300, 400)
    window.connect("destroy", Gtk.main_quit)
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    for row in range(200):
        column.pack_start(Gtk.Label(label=f"Row {row}", xalign=0), False, False, 4)
    scroller.add(column)
    adjustment = scroller.get_vadjustment()
    adjustment.connect("value-changed", lambda a: say("scroll", round(a.get_value())))
    window.add(scroller)
    window.show_all()
    Gtk.main()


def main():
    if sys.argv[1] == "--scroll":
        scroll_probe(sys.argv[2])
        return
    window = Gtk.Window(title=sys.argv[1])
    window.set_default_size(300, 400)
    window.connect("destroy", Gtk.main_quit)
    fixed = Gtk.Fixed()
    window.add(fixed)
    combo = Gtk.ComboBoxText()
    for choice in CHOICES:
        combo.append_text(choice)
    combo.set_active(0)
    combo.connect("changed", lambda c: say("changed", c.get_active_text()))
    combo.connect("notify::popup-shown", popup_shown)
    fixed.put(combo, 20, 200)
    window.show_all()
    Gtk.main()


main()
