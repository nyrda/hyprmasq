"""The form task's questionnaire as a Tk app: form_tk.py STATE-DIR COUNT [STATE-FD].

The same questions, layout and records as form.py, the GTK 4 app, in Tk's widgets: check buttons, a scale, an
entry, a combo box, whose list opens in a window of its own, and a column in a canvas that scrolls. Tk runs on X11
only. check.py grades either app's state.

Every change of an answer is logged with the time to STATE-DIR/events.jsonl, with how a slider moved, named as
form.py names GTK's scroll types (jump for the pointer, step-forward and the like for keys), and every press of
Submit to submissions.jsonl. On SIGUSR1 it writes its state from its memory to the file descriptor STATE-FD, or
without it to STATE-DIR/state.json.
"""

import json
import os
import signal
import sys
import time
import tkinter as tk
from tkinter import font, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import items  # noqa: E402

# The keys of Tk's scale bindings by the names form.py records for GTK's scroll types.
KEYS = {
    "Left": "step-backward", "Up": "step-backward", "Right": "step-forward", "Down": "step-forward",
    "Control-Left": "page-backward", "Control-Up": "page-backward", "Control-Right": "page-forward",
    "Control-Down": "page-forward", "Home": "start", "End": "end",
}
WHEEL = ("<Button-4>", "<Button-5>", "<MouseWheel>")


class Form:
    def __init__(self, root, state_dir, count, state_fd):
        self.state_dir = state_dir
        self.state_fd = state_fd
        self.items = items.items(count)
        self.values = {item["number"]: items.initial(item) for item in self.items}
        self.events = []
        self.submissions = []

        root.title("Questionnaire")
        root.geometry("1000x800")
        base = font.nametofont("TkDefaultFont")
        base.configure(size=11)
        font.nametofont("TkTextFont").configure(size=11)
        size = base.actual("size")
        self.fonts = {
            "number": base.copy(), "prompt": base.copy(), "heading": base.copy(),
            "target": font.Font(family="monospace", size=round(size * 1.15), weight="bold"),
            "value": font.Font(family="monospace", size=round(size * 1.25), weight="bold"),
        }
        self.fonts["number"].configure(weight="bold")
        self.fonts["prompt"].configure(size=round(size * 1.15))
        self.fonts["heading"].configure(size=size * 2, weight="bold")
        style = ttk.Style(root)
        style.theme_use("clam")
        self.background = style.lookup("TFrame", "background")
        style.configure("Question.TFrame", relief="solid", borderwidth=1, bordercolor="#d4d4d4")
        style.configure("Dim.TLabel", foreground="#6f6f6f")
        style.configure("Number.TLabel", foreground="#6f6f6f", font=self.fonts["number"])

        # The column in a canvas, which scrolls it, centered at a fixed width.
        self.canvas = tk.Canvas(root, highlightthickness=0, background=self.background)
        bar = ttk.Scrollbar(root, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        column = ttk.Frame(self.canvas, width=760, padding=(0, 24, 0, 48))
        self.column = self.canvas.create_window(0, 0, window=column, anchor="n", width=760)
        column.bind("<Configure>", self.layout)
        self.canvas.bind("<Configure>", self.layout)

        ttk.Label(column, text="Questionnaire", font=self.fonts["heading"]).pack(anchor="w")
        ttk.Label(column, text=f"{count} questions. Answer each one as it says, then press Submit at the end.",
                  wraplength=760).pack(anchor="w", pady=(14, 0))
        for item in self.items:
            self.question(column, item, count).pack(fill="x", pady=(14, 0))

        footer = ttk.Frame(column)
        footer.pack(fill="x", pady=(24, 0))
        ttk.Button(footer, text="Submit", command=self.submit).pack(side="left")
        self.status = ttk.Label(footer, text="")
        self.status.pack(side="left", padx=18)

        # The wheel scrolls the page anywhere but over a combo box, whose own binding moves through its choices, as
        # Qt's and GTK 3's do. A slider binds it to the page itself, ahead of any binding of its class.
        for sequence in WHEEL:
            root.bind_all(sequence, self.scroll_page)
            root.bind_class("TCombobox", sequence, "+break")

    def layout(self, _event):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.coords(self.column, width / 2, 0)
        # GtkScrolledWindow's distance for one step of a wheel.
        self.canvas.configure(scrollregion=(0, 0, width, self.canvas.bbox("all")[3]),
                              yscrollincrement=round(height ** (2 / 3)))

    def scroll_page(self, event):
        if event.num == 4 or event.delta > 0:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5 or event.delta < 0:
            self.canvas.yview_scroll(1, "units")
        return "break"

    def question(self, parent, item, count):
        frame = ttk.Frame(parent, style="Question.TFrame", padding=(18, 14))
        ttk.Label(frame, text=f"Question {item['number']} of {count}", style="Number.TLabel").pack(anchor="w")
        ttk.Label(frame, text=item["question"], font=self.fonts["prompt"], wraplength=720).pack(
            anchor="w", pady=(10, 0))
        getattr(self, item["kind"])(frame, item).pack(fill="x", anchor="w", pady=(10, 0))
        return frame

    def checkbox(self, parent, item):
        row = ttk.Frame(parent)
        ticked = []
        for option in item["options"]:
            var = tk.BooleanVar(value=False)
            ttk.Checkbutton(row, text=option, variable=var, command=lambda: self.change(
                item, [o for o, v in zip(item["options"], ticked) if v.get()])).pack(side="left", padx=(0, 22))
            ticked.append(var)
        return row

    def slider(self, parent, item):
        step, lowest = item["step"], item["lowest"]
        box = ttk.Frame(parent)
        ttk.Label(box, text=items.range_text(item), style="Dim.TLabel").pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        # Tk's scale, not ttk's: its resolution snaps the value to the step, for the pointer and the keys.
        scale = tk.Scale(row, from_=lowest, to=item["highest"], resolution=step, orient="horizontal",
                         showvalue=False, bigincrement=step * 10, sliderlength=24, width=16,
                         highlightthickness=2, highlightbackground=self.background, background=self.background,
                         borderwidth=1)
        scale.set(item["start"])
        value = ttk.Label(row, text=items.show(item["start"], step), font=self.fonts["value"], width=8)
        how = {"key": None}

        def key(event):
            control = "Control-" if event.state & 0x4 else ""
            how["key"] = KEYS.get(control + event.keysym)

        def press(_event):
            # A click focuses the slider, so that the keys move it after, as in the other toolkits.
            how["key"] = None
            scale.focus_set()

        def changed(position):
            shown = items.show(float(position), step)
            value.configure(text=shown)
            self.change(item, shown, how=how["key"] or "jump")
            how["key"] = None

        scale.bind("<KeyPress>", key)
        scale.bind("<ButtonPress>", press)
        for sequence in WHEEL:
            scale.bind(sequence, self.scroll_page)
        scale.configure(command=changed)

        ttk.Label(row, text=items.show(lowest, step), style="Dim.TLabel").pack(side="left")
        scale.pack(side="left", fill="x", expand=True, padx=18)
        ttk.Label(row, text=items.show(item["highest"], step), style="Dim.TLabel").pack(side="left")
        ttk.Label(row, text="Value:").pack(side="left", padx=(18, 18))
        value.pack(side="left")
        return box

    def text(self, parent, item):
        box = ttk.Frame(parent)
        tk.Label(box, text=item["answer"], font=self.fonts["target"], background="#e8e8e8", padx=8,
                 pady=2).pack(anchor="w")
        var = tk.StringVar(value="")
        var.trace_add("write", lambda *_: self.change(item, var.get()))
        ttk.Entry(box, textvariable=var, width=34).pack(anchor="w", pady=(10, 0))
        return box

    def dropdown(self, parent, item):
        box = ttk.Frame(parent)
        dropdown = ttk.Combobox(box, values=item["options"], state="readonly",
                                width=max(len(o) for o in item["options"]) + 2)
        dropdown.current(0)
        dropdown.bind("<<ComboboxSelected>>", lambda _e: self.change(item, dropdown.get()))
        dropdown.pack(anchor="w")
        return box

    def change(self, item, value, how=None):
        if value == self.values[item["number"]]:
            return
        self.values[item["number"]] = value
        event = {"time": time.time(), "number": item["number"], "kind": item["kind"], "value": value}
        if how:
            event["how"] = how
        self.events.append(event)
        self.append("events.jsonl", event)

    def submit(self):
        submission = {"time": time.time(), "values": {str(n): v for n, v in self.values.items()}}
        self.submissions.append(submission)
        self.append("submissions.jsonl", submission)
        self.status.configure(text=f"Submitted at {time.strftime('%H:%M:%S', time.localtime(submission['time']))}.")

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
            return
        path = os.path.join(self.state_dir, "state.json")
        with open(path + ".tmp", "w") as f:
            json.dump(state, f, indent=2)
        os.replace(path + ".tmp", path)


def main():
    state_dir, count = sys.argv[1], int(sys.argv[2])
    state_fd = int(sys.argv[3]) if len(sys.argv) > 3 else None
    items.items(count)
    os.makedirs(state_dir, exist_ok=True)
    # The X11 class, as the GTK app's application id.
    root = tk.Tk(className="Questionnaire")
    form = Form(root, state_dir, count, state_fd)
    # Python runs signal handlers between bytecodes, and Tk's loop runs none while idle: a timer lets it.
    signal.signal(signal.SIGUSR1, lambda *_: form.dump())

    def tick():
        root.after(100, tick)

    tick()
    root.mainloop()


if __name__ == "__main__":
    main()
