"""The form task's questionnaire, a GTK 4 app: form.py STATE-DIR COUNT [STATE-FD].

It asks the first COUNT questions of items.py, one below the other in a scrolled column, with Submit at the end.
Every change of an answer is logged with the time to STATE-DIR/events.jsonl, with how a slider moved (GTK's scroll
type: jump for the pointer, step-forward and the like for keys), and every press of Submit to submissions.jsonl.
On SIGUSR1 it writes its state from its memory, the answers on screen, the submissions and the events, to the file
descriptor STATE-FD, or without it to STATE-DIR/state.json. The benchmark passes a file that only root can open,
which the grade reads, so that only this process's widgets decide it.
"""

import json
import os
import signal
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import items  # noqa: E402

CSS = """
.question { border: 1px solid alpha(currentColor, 0.15); border-radius: 8px; padding: 14px 18px; }
.number { font-weight: bold; opacity: 0.6; }
.prompt { font-size: 1.15em; }
.target { font-family: monospace; font-size: 1.15em; font-weight: bold; padding: 2px 8px;
          background: alpha(currentColor, 0.08); border-radius: 4px; }
.value { font-family: monospace; font-size: 1.25em; font-weight: bold; }
"""


class Form:
    def __init__(self, app, state_dir, count, state_fd):
        self.state_dir = state_dir
        self.state_fd = state_fd
        self.items = items.items(count)
        self.values = {item["number"]: items.initial(item) for item in self.items}
        self.events = []
        self.submissions = []

        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)

        self.window = Gtk.ApplicationWindow(application=app, title="Questionnaire")
        self.window.set_default_size(1000, 800)
        Gtk.StyleContext.add_provider_for_display(self.window.get_display(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.window.set_child(self.scroller)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, halign=Gtk.Align.CENTER,
                         margin_top=24, margin_bottom=48, margin_start=24, margin_end=24, width_request=760)
        self.scroller.set_child(column)
        title = Gtk.Label(label="Questionnaire", xalign=0, css_classes=["title-1"])
        intro = Gtk.Label(xalign=0, wrap=True, label=f"{count} questions. Answer each one as it says, "
                                                     "then press Submit at the end.")
        column.append(title)
        column.append(intro)
        for item in self.items:
            column.append(self.question(item, count))

        submit = Gtk.Button(label="Submit", halign=Gtk.Align.START, css_classes=["suggested-action", "pill"])
        submit.connect("clicked", self.submit)
        self.status = Gtk.Label(xalign=0, label="")
        footer = Gtk.Box(spacing=18, margin_top=10)
        footer.append(submit)
        footer.append(self.status)
        column.append(footer)

        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self.dump)
        self.window.present()

    def question(self, item, count):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["question"])
        box.append(Gtk.Label(label=f"Question {item['number']} of {count}", xalign=0, css_classes=["number"]))
        box.append(Gtk.Label(label=item["question"], xalign=0, wrap=True, css_classes=["prompt"]))
        box.append(getattr(self, item["kind"])(item))
        return box

    def checkbox(self, item):
        row = Gtk.Box(spacing=22)
        buttons = []
        for option in item["options"]:
            button = Gtk.CheckButton(label=option)
            button.connect("toggled", lambda _b: self.change(item, [o for o, b in zip(item["options"], buttons)
                                                                   if b.get_active()]))
            buttons.append(button)
            row.append(button)
        return row

    def slider(self, item):
        step = item["step"]
        adjustment = Gtk.Adjustment(lower=item["lowest"], upper=item["highest"], value=item["start"],
                                    step_increment=step, page_increment=step * 10)
        scale = Gtk.Scale(adjustment=adjustment, draw_value=False, digits=items.decimals(step), hexpand=True)
        value = Gtk.Label(label=items.show(item["start"], step), xalign=0, width_chars=8, css_classes=["value"])
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
        # The wheel over a slider scrolls the page, as over the other questions, instead of moving the slider.
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", self.scroll_page)
        scale.add_controller(wheel)

        row = Gtk.Box(spacing=18)
        row.append(Gtk.Label(label=items.show(item["lowest"], step), css_classes=["dim-label"]))
        row.append(scale)
        row.append(Gtk.Label(label=items.show(item["highest"], step), css_classes=["dim-label"]))
        row.append(Gtk.Label(label="Value:"))
        row.append(value)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(Gtk.Label(label=items.range_text(item), xalign=0, css_classes=["dim-label"]))
        box.append(row)
        return box

    def scroll_page(self, _controller, _dx, dy):
        page = self.scroller.get_vadjustment()
        # GtkScrolledWindow's own distance for one step of a wheel.
        distance = page.get_page_size() ** (2 / 3)
        page.set_value(min(max(page.get_value() + dy * distance, page.get_lower()),
                           page.get_upper() - page.get_page_size()))
        return True

    def text(self, item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(Gtk.Label(label=item["answer"], xalign=0, halign=Gtk.Align.START, css_classes=["target"]))
        entry = Gtk.Entry(width_chars=32, halign=Gtk.Align.START)
        entry.connect("changed", lambda e: self.change(item, e.get_text()))
        box.append(entry)
        return box

    def dropdown(self, item):
        dropdown = Gtk.DropDown.new_from_strings(item["options"])
        dropdown.set_halign(Gtk.Align.START)
        dropdown.connect("notify::selected", lambda d, _p: self.change(item, item["options"][d.get_selected()]))
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
