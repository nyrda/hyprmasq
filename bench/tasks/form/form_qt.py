"""The form task's questionnaire as a Qt app: form_qt.py STATE-DIR COUNT [STATE-FD].

The same questions, layout and records as form.py, the GTK 4 app, for platforms where GTK exposes no accessibility,
such as macOS, whose GTK builds lack the AccessKit backend: Qt gives its widgets to the platform's accessibility
API there and to AT-SPI on Linux. check.py grades either app's state.

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

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (QAbstractSlider, QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import items  # noqa: E402

STYLE = """
#column { background: palette(base); }
QFrame[class="question"] { border: 1px solid palette(mid); border-radius: 8px; }
QLabel[class="number"] { font-weight: bold; color: palette(placeholder-text); }
QLabel[class="prompt"] { font-size: 15px; }
QLabel[class="target"] { font-family: monospace; font-size: 15px; font-weight: bold; padding: 2px 8px;
                         background: palette(alternate-base); border-radius: 4px; }
QLabel[class="value"] { font-family: monospace; font-size: 16px; font-weight: bold; }
QLabel[class="dim"] { color: palette(placeholder-text); }
QLabel[class="heading"] { font-size: 26px; font-weight: bold; }
"""

# Qt's slider actions by the names form.py records for GTK's scroll types.
ACTIONS = {
    QAbstractSlider.SliderAction.SliderSingleStepAdd: "step-forward",
    QAbstractSlider.SliderAction.SliderSingleStepSub: "step-backward",
    QAbstractSlider.SliderAction.SliderPageStepAdd: "page-forward",
    QAbstractSlider.SliderAction.SliderPageStepSub: "page-backward",
    QAbstractSlider.SliderAction.SliderToMinimum: "start",
    QAbstractSlider.SliderAction.SliderToMaximum: "end",
    QAbstractSlider.SliderAction.SliderMove: "jump",
}


def label(text, cls=None, wrap=False):
    widget = QLabel(text)
    widget.setWordWrap(wrap)
    if cls:
        widget.setProperty("class", cls)
    return widget


class WheelToPage(QObject):
    """The wheel over a slider scrolls the page, as over the other questions, instead of moving the slider."""

    def __init__(self, scroller):
        super().__init__()
        self.scroller = scroller

    def eventFilter(self, _watched, event):
        if event.type() == QEvent.Type.Wheel:
            QApplication.sendEvent(self.scroller.viewport(), event)
            return True
        return False


class Form(QMainWindow):
    def __init__(self, state_dir, count, state_fd):
        super().__init__()
        self.state_dir = state_dir
        self.state_fd = state_fd
        self.items = items.items(count)
        self.values = {item["number"]: items.initial(item) for item in self.items}
        self.events = []
        self.submissions = []

        self.setWindowTitle("Questionnaire")
        self.resize(1000, 800)
        self.scroller = QScrollArea()
        self.scroller.setWidgetResizable(True)
        self.scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setCentralWidget(self.scroller)
        self.wheel = WheelToPage(self.scroller)

        page = QWidget()
        page.setObjectName("column")
        outer = QHBoxLayout(page)
        column_widget = QWidget()
        column_widget.setFixedWidth(760)
        column = QVBoxLayout(column_widget)
        column.setSpacing(14)
        column.setContentsMargins(0, 24, 0, 48)
        outer.addStretch()
        outer.addWidget(column_widget)
        outer.addStretch()
        self.scroller.setWidget(page)

        column.addWidget(label("Questionnaire", "heading"))
        column.addWidget(label(f"{count} questions. Answer each one as it says, then press Submit at the end.",
                               wrap=True))
        for item in self.items:
            column.addWidget(self.question(item, count))

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 10, 0, 0)
        submit = QPushButton("Submit")
        submit.clicked.connect(self.submit)
        self.status = QLabel("")
        footer.addWidget(submit)
        footer.addSpacing(18)
        footer.addWidget(self.status)
        footer.addStretch()
        column.addLayout(footer)
        column.addStretch()

    def question(self, item, count):
        frame = QFrame()
        frame.setProperty("class", "question")
        box = QVBoxLayout(frame)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(10)
        box.addWidget(label(f"Question {item['number']} of {count}", "number"))
        box.addWidget(label(item["question"], "prompt", wrap=True))
        box.addWidget(getattr(self, item["kind"])(item))
        return frame

    def checkbox(self, item):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(22)
        buttons = []
        for option in item["options"]:
            button = QCheckBox(option)
            button.toggled.connect(lambda _checked: self.change(item, [o for o, b in zip(item["options"], buttons)
                                                                       if b.isChecked()]))
            buttons.append(button)
            layout.addWidget(button)
        layout.addStretch()
        return row

    def slider(self, item):
        step, lowest = item["step"], item["lowest"]
        # A QSlider holds integers: its positions are the steps from the lowest value.
        positions = round((item["highest"] - lowest) / step)
        scale = QSlider(Qt.Orientation.Horizontal)
        scale.setRange(0, positions)
        scale.setSingleStep(1)
        scale.setPageStep(10)
        scale.setValue(round((item["start"] - lowest) / step))
        scale.setAccessibleName(f"Question {item['number']} slider")
        scale.installEventFilter(self.wheel)
        value = label(items.show(item["start"], step), "value")
        value.setMinimumWidth(90)
        how = {"action": None}

        def action(kind):
            how["action"] = ACTIONS.get(QAbstractSlider.SliderAction(kind))

        def changed(position):
            shown = items.show(lowest + position * step, step)
            value.setText(shown)
            self.change(item, shown, how=how["action"] or "jump")
            how["action"] = None

        scale.actionTriggered.connect(action)
        scale.valueChanged.connect(changed)

        row = QHBoxLayout()
        row.setSpacing(18)
        row.addWidget(label(items.show(lowest, step), "dim"))
        row.addWidget(scale, 1)
        row.addWidget(label(items.show(item["highest"], step), "dim"))
        row.addWidget(QLabel("Value:"))
        row.addWidget(value)
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(label(items.range_text(item), "dim"))
        layout.addLayout(row)
        return box

    def text(self, item):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        target = label(item["answer"], "target")
        target.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(target, 0, Qt.AlignmentFlag.AlignLeft)
        entry = QLineEdit()
        entry.setFixedWidth(entry.fontMetrics().averageCharWidth() * 34)
        entry.setAccessibleName(f"Question {item['number']} text")
        entry.textChanged.connect(lambda text: self.change(item, text))
        layout.addWidget(entry, 0, Qt.AlignmentFlag.AlignLeft)
        return box

    def dropdown(self, item):
        box = QWidget()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        dropdown = QComboBox()
        dropdown.addItems(item["options"])
        dropdown.setAccessibleName(f"Question {item['number']} choice")
        dropdown.currentIndexChanged.connect(lambda index: self.change(item, item["options"][index]))
        layout.addWidget(dropdown)
        layout.addStretch()
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
        self.status.setText(f"Submitted at {time.strftime('%H:%M:%S', time.localtime(submission['time']))}.")

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
    app = QApplication([sys.argv[0]])
    # The Wayland app id and macOS bundle-less name, as the GTK app's application id.
    app.setDesktopFileName("org.example.Questionnaire")
    app.setApplicationName("Questionnaire")
    app.setStyleSheet(STYLE)
    form = Form(state_dir, count, state_fd)
    # Python runs signal handlers between bytecodes, and Qt's loop runs none while idle: a timer lets it.
    signal.signal(signal.SIGUSR1, lambda *_: form.dump())
    tick = QTimer()
    tick.start(100)
    tick.timeout.connect(lambda: None)
    form.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
