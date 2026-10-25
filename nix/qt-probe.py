"""VM fixture: a Qt 6 Widgets app in PySide6, as the form task's questionnaire is in its Qt port, that logs what it gets.

    hyprmasq-qt-probe TITLE

Its window TITLE, of app id hyprmasq-qt-probe, has the questionnaire's widgets at fixed places, in window-local logical
pixels: a check box at 20,20 160×30, a slider from 0 to 50 at 20,70 300×30, a line edit at 20,120 300×30 and a
dropdown of the planets at 20,170 200×30, whose list opens in a popup. Qt draws widgets into shm buffers, also in the
VM, and commits a frame only while the window is exposed: not while the compositor tells it that it is suspended, nor
from 100 ms after it asked for a frame callback that has not come until that callback comes.

It logs to stdout after a CLOCK_MONOTONIC timestamp in milliseconds: "configured W H suspended" or "configured W H
shown" for each xdg_toplevel configure that Qt applies, which it reads from Qt's own log, "exposed 1" or "exposed 0"
when Qt exposes the window or stops, and "checked 0|1", "slider N", "text T" and "choice C" for each change of a
widget. Qt's other messages go to stderr.

With --scroll before TITLE it instead shows a fixed check box above 200 rows.
It logs "scroll VALUE VIEWPORT TOTAL" in logical pixels when the page moves.
"""

import re
import sys
import time

from PySide6.QtCore import QEvent, QLoggingCategory, QObject, QtMsgType, Qt, qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLineEdit, QMainWindow, QSlider, QWidget
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout

PLANETS = ["Mercury", "Venus", "Earth", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"]
# Qt's Wayland backend logs each configure that it applies, with the toplevel's size and whether it is suspended.
APPLIED = re.compile(r"Applied pending xdg_toplevel configure event: QSize\((\d+), (\d+)\).*suspended\s+(true|false)")


def say(line):
    print(f"{time.clock_gettime(time.CLOCK_MONOTONIC) * 1000:.0f} {line}", flush=True)


def message(kind, context, text):
    if context.category == "qt.qpa.wayland" and kind == QtMsgType.QtDebugMsg:
        if applied := APPLIED.search(text):
            width, height, suspended = applied.groups()
            say(f"configured {width} {height} {'suspended' if suspended == 'true' else 'shown'}")
        return
    print(f"{context.category}: {text}", file=sys.stderr, flush=True)


class Exposure(QObject):
    """Logs when Qt exposes the window or stops."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.exposed = None

    def eventFilter(self, _watched, event):
        if event.type() == QEvent.Type.Expose and self.window.isExposed() != self.exposed:
            self.exposed = self.window.isExposed()
            say(f"exposed {int(self.exposed)}")
        return False


def main():
    scrolling = len(sys.argv) == 3 and sys.argv[1] == "--scroll"
    if len(sys.argv) != 2 and not scrolling:
        print("usage: hyprmasq-qt-probe [--scroll] TITLE", file=sys.stderr)
        sys.exit(2)
    qInstallMessageHandler(message)
    QLoggingCategory.setFilterRules("qt.qpa.wayland.debug=true")
    app = QApplication([sys.argv[0], "-platform", "wayland"])
    app.setDesktopFileName("hyprmasq-qt-probe")

    window = QMainWindow()
    window.setWindowTitle(sys.argv[-1])
    window.resize(600, 400)
    if scrolling:
        container = QWidget()
        layout = QVBoxLayout(container)
        check = QCheckBox("Enabled")
        check.toggled.connect(lambda checked: say(f"checked {int(checked)}"))
        layout.addWidget(check)
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        column = QWidget()
        rows = QVBoxLayout(column)
        for row in range(200):
            label = QLabel(f"Row {row}")
            label.setMinimumHeight(32)
            rows.addWidget(label)
        scroller.setWidget(column)
        layout.addWidget(scroller)
        bar = scroller.verticalScrollBar()
        bar.valueChanged.connect(lambda value: say(f"scroll {value} {bar.pageStep()} {bar.maximum()+bar.pageStep()}"))
        window.setCentralWidget(container)
        window.show()
        sys.exit(app.exec())
    page = QWidget()
    window.setCentralWidget(page)
    check = QCheckBox("Amber", page)
    check.setGeometry(20, 20, 160, 30)
    check.toggled.connect(lambda checked: say(f"checked {int(checked)}"))
    slider = QSlider(Qt.Orientation.Horizontal, page)
    slider.setRange(0, 50)
    slider.setGeometry(20, 70, 300, 30)
    slider.valueChanged.connect(lambda value: say(f"slider {value}"))
    line = QLineEdit(page)
    line.setGeometry(20, 120, 300, 30)
    line.textChanged.connect(lambda text: say(f"text {text}"))
    dropdown = QComboBox(page)
    dropdown.addItems(PLANETS)
    dropdown.setGeometry(20, 170, 200, 30)
    dropdown.currentTextChanged.connect(lambda text: say(f"choice {text}"))

    window.show()
    exposure = Exposure(window.windowHandle())
    window.windowHandle().installEventFilter(exposure)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
