"""The form task's questionnaire as a GTK 3 app: form_gtk3.py STATE-DIR COUNT [STATE-FD].

The same questions, layout and records as form.py, the GTK 4 app, in GTK 3's widgets: check buttons, a scale, an
entry, a combo box, whose choices open in a menu, and a scrolled column. check.py grades either app's state.

Every change of an answer is logged with the time to STATE-DIR/events.jsonl, with how a slider moved (GTK's scroll
type: jump for the pointer, step-forward and the like for keys), and every press of Submit to submissions.jsonl. On
SIGUSR1 it writes its state from its memory to the file descriptor STATE-FD, or without it to
STATE-DIR/state.json.
"""

import json
import os
import signal
import sys
import time

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib  # noqa: E402

# The Wayland app id and the X11 class, as the GTK 4 app's application id. GTK 3 takes the application id for them
# only on a session bus, and otherwise the program's name, which it reads when Gtk is imported and capitalizes for
# the class.
GLib.set_prgname("org.example.Questionnaire")
from gi.repository import Gio, Gtk  # noqa: E402

Gdk.set_program_class("org.example.Questionnaire")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import items  # noqa: E402

CSS = b"""
.question { border-radius: 8px; }
.question > border { border: 1px solid alpha(currentColor, 0.15); border-radius: 8px; }
.number { font-weight: bold; opacity: 0.6; }
.prompt { font-size: 1.15em; }
.target { font-family: monospace; font-size: 1.15em; font-weight: bold; padding: 2px 8px;
          background: alpha(currentColor, 0.08); border-radius: 4px; }
.value { font-family: monospace; font-size: 1.25em; font-weight: bold; }
.heading { font-size: 2em; font-weight: 800; }
"""


def label(text, cls=None, wrap=False, **properties):
    widget = Gtk.Label(label=text, xalign=0, wrap=wrap, **properties)
    if cls:
        widget.get_style_context().add_class(cls)
    return widget


class Form:
    def __init__(self, app, state_dir, count, state_fd):
        self.state_dir = state_dir
        self.state_fd = state_fd
        self.items = items.items(count)
        self.values = {item["number"]: items.initial(item) for item in self.items}
        self.events = []
        self.submissions = []

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.window = Gtk.ApplicationWindow(application=app, title="Questionnaire")
        self.window.set_default_size(1000, 800)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.window.add(self.scroller)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, halign=Gtk.Align.CENTER,
                         margin_top=24, margin_bottom=48, margin_start=24, margin_end=24, width_request=760)
        self.scroller.add(column)
        column.pack_start(label("Questionnaire", "heading"), False, False, 0)
        column.pack_start(label(f"{count} questions. Answer each one as it says, then press Submit at the end.",
                                wrap=True), False, False, 0)
        for item in self.items:
            column.pack_start(self.question(item, count), False, False, 0)

        submit = Gtk.Button(label="Submit", halign=Gtk.Align.START)
        submit.get_style_context().add_class("suggested-action")
        submit.connect("clicked", self.submit)
        self.status = label("")
        footer = Gtk.Box(spacing=18, margin_top=10)
        footer.pack_start(submit, False, False, 0)
        footer.pack_start(self.status, False, False, 0)
        column.pack_start(footer, False, False, 0)

        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self.dump)
        self.window.show_all()

    def question(self, item, count):
        # A GTK 3 box draws no border of its own: a frame does.
        frame = Gtk.Frame()
        frame.get_style_context().add_class("question")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14, margin_bottom=14,
                      margin_start=18, margin_end=18)
        box.pack_start(label(f"Question {item['number']} of {count}", "number"), False, False, 0)
        box.pack_start(label(item["question"], "prompt", wrap=True), False, False, 0)
        box.pack_start(getattr(self, item["kind"])(item), False, False, 0)
        frame.add(box)
        return frame

    def checkbox(self, item):
        row = Gtk.Box(spacing=22)
        buttons = []
        for option in item["options"]:
            button = Gtk.CheckButton(label=option)
            button.connect("toggled", lambda _b: self.change(item, [o for o, b in zip(item["options"], buttons)
                                                                   if b.get_active()]))
            buttons.append(button)
            row.pack_start(button, False, False, 0)
        return row

    def slider(self, item):
        step = item["step"]
        adjustment = Gtk.Adjustment(lower=item["lowest"], upper=item["highest"], value=item["start"],
                                    step_increment=step, page_increment=step * 10)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment, draw_value=False,
                          digits=items.decimals(step), hexpand=True)
        value = label(items.show(item["start"], step), "value", width_chars=8)
        how = {"scroll": None}

        def snap(_scale, scroll, wanted):
            # The value lands on the step, whether the pointer or a key moved it.
            snapped = item["lowest"] + round((wanted - item["lowest"]) / step) * step
            how["scroll"] = scroll.value_nick
            adjustment.set_value(min(max(snapped, item["lowest"]), item["highest"]))
            return True

        def changed(_adjustment):
            shown = items.show(adjustment.get_value(), step)
            value.set_label(shown)
            self.change(item, shown, how=how["scroll"])

        scale.connect("change-value", snap)
        adjustment.connect("value-changed", changed)
        # The wheel over a slider scrolls the page, as over the other questions, instead of moving the slider:
        # this handler runs before GtkRange's own and stops it.
        scale.connect("scroll-event", self.scroll_page)

        row = Gtk.Box(spacing=18)
        row.pack_start(label(items.show(item["lowest"], step), "dim-label"), False, False, 0)
        row.pack_start(scale, True, True, 0)
        row.pack_start(label(items.show(item["highest"], step), "dim-label"), False, False, 0)
        row.pack_start(label("Value:"), False, False, 0)
        row.pack_start(value, False, False, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.pack_start(label(items.range_text(item), "dim-label"), False, False, 0)
        box.pack_start(row, False, False, 0)
        return box

    def scroll_page(self, _widget, event):
        found, _dx, dy = event.get_scroll_deltas()
        if not found:
            dy = {Gdk.ScrollDirection.UP: -1, Gdk.ScrollDirection.DOWN: 1}.get(event.direction, 0)
        page = self.scroller.get_vadjustment()
        # GtkScrolledWindow's own distance for one step of a wheel.
        distance = page.get_page_size() ** (2 / 3)
        page.set_value(min(max(page.get_value() + dy * distance, page.get_lower()),
                           page.get_upper() - page.get_page_size()))
        return True

    def text(self, item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.pack_start(label(item["answer"], "target", halign=Gtk.Align.START), False, False, 0)
        entry = Gtk.Entry(width_chars=32, halign=Gtk.Align.START)
        entry.connect("changed", lambda e: self.change(item, e.get_text()))
        box.pack_start(entry, False, False, 0)
        return box

    def dropdown(self, item):
        dropdown = Gtk.ComboBoxText(halign=Gtk.Align.START)
        for option in item["options"]:
            dropdown.append_text(option)
        dropdown.set_active(0)
        dropdown.connect("changed", lambda d: self.change(item, item["options"][d.get_active()]))
        return dropdown

    def change(self, item, value, how=None):
        if value == self.values[item["number"]]:
            return
        self.values[item["number"]] = value
        event = {"time": time.time(), "number": item["number"], "kind": item["kind"], "value": value}
        if how:
            event["how"] = how
        self.events.append(event)
        self.append("events.jsonl", event)

    def submit(self, _button):
        submission = {"time": time.time(), "values": {str(n): v for n, v in self.values.items()}}
        self.submissions.append(submission)
        self.append("submissions.jsonl", submission)
        self.status.set_label(f"Submitted at {time.strftime('%H:%M:%S', time.localtime(submission['time']))}.")

    def append(self, name, record):
        with open(os.path.join(self.state_dir, name), "a") as f:
            f.write(json.dumps(record) + "\n")

    def dump(self):
        state = {"count": len(self.items), "values": {str(n): v for n, v in self.values.items()},
                 "submissions": self.submissions, "events": self.events}
        if self.state_fd is not None:
            data = json.dumps(state, indent=2).encode()
            os.ftruncate(self.state_fd, 0)
            os.pwrite(self.state_fd, data, 0)
            return GLib.SOURCE_CONTINUE
        path = os.path.join(self.state_dir, "state.json")
        with open(path + ".tmp", "w") as f:
            json.dump(state, f, indent=2)
        os.replace(path + ".tmp", path)
        return GLib.SOURCE_CONTINUE


def main():
    state_dir, count = sys.argv[1], int(sys.argv[2])
    state_fd = int(sys.argv[3]) if len(sys.argv) > 3 else None
    items.items(count)
    os.makedirs(state_dir, exist_ok=True)
    app = Gtk.Application(application_id="org.example.Questionnaire", flags=Gio.ApplicationFlags.NON_UNIQUE)
    forms = []
    app.connect("activate", lambda a: forms.append(Form(a, state_dir, count, state_fd)))
    sys.exit(app.run([sys.argv[0]]))


if __name__ == "__main__":
    main()
