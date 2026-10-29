"""The questionnaire's 40 questions, for the app (form.py) and the grade (check.py). A run asks the first N of them,
N the task's variant, from 10 to 40. The kinds take turns, so that every N asks each about as often.

Each question says what its answer is. Nothing is to be worked out or looked up: the task measures how fast an agent
gets through a chore of input, not what it knows.

- checkbox: tick exactly the options it names, among the ones shown; none is ticked at the start.
- slider: move the slider until its read-only value shows the number it names. The value snaps to the step, which
  a caption gives with the range.
- text: type exactly the text it shows into the field. Spaces around it do not count.
- dropdown: choose the option it names. The dropdown starts at "Choose…", which is no answer.
"""

import os

MIN_ITEMS, MAX_ITEMS, DEFAULT_ITEMS = 10, 40, 25
PLACEHOLDER = "Choose…"

CHECKBOXES = [
    (["Amber", "Beige", "Cobalt", "Denim", "Olive", "Plum"], ["Amber", "Cobalt", "Olive"]),
    (["2", "3", "5", "7", "11", "13"], ["7"]),
    (["Anvil", "Kettle", "Ladder", "Lantern", "Mirror"], ["Kettle", "Lantern"]),
    (["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
     ["Tuesday", "Thursday", "Saturday"]),
    (["P", "Q", "R", "S", "T"], ["Q"]),
    (["Badger", "Heron", "Lynx", "Otter", "Raven", "Stoat"], ["Heron", "Otter"]),
    (["0.5", "1.0", "1.5", "2.0", "2.5", "3.0"], ["0.5", "2.5"]),
    (["Basalt", "Chalk", "Flint", "Granite", "Marble", "Slate"], ["Granite"]),
    (["North", "South", "East", "West", "Up", "Down"], ["North", "East", "West"]),
    (["Cello", "Flute", "Harp", "Oboe", "Tuba", "Violin"], ["Oboe", "Violin"]),
]

# (lowest, highest, step, start, target)
SLIDERS = [
    (0, 100, 1, 0, 37),
    (-50, 50, 1, 0, -18),
    (0, 1000, 5, 500, 735),
    (0, 10, 0.1, 0, 6.4),
    (1, 12, 1, 1, 9),
    (0, 360, 1, 180, 271),
    (-1, 1, 0.05, 0, 0.35),
    (100, 200, 1, 100, 163),
    (0, 5000, 25, 0, 3175),
    (0, 100, 0.5, 50, 12.5),
]

TEXTS = [
    "Northwind 42",
    "blue-green-07",
    "Ada Lovelace",
    "3.14159",
    "QX-7731-B",
    "the quick brown fox",
    "Tuesday, 14:30",
    "mara@example.test",
    "1,024 units",
    "Echo/Foxtrot",
]

DROPDOWNS = [
    (["Mercury", "Venus", "Earth", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"], "Saturn"),
    (["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
      "November", "December"], "March"),
    (["Small", "Medium", "Large", "Extra large"], "Medium"),
    (["Apple", "Cherry", "Fig", "Grape", "Lemon", "Lime", "Mango", "Peach"], "Lime"),
    (["8", "16", "32", "64", "128", "256"], "64"),
    (["Berlin", "Dublin", "Lisbon", "Madrid", "Oslo", "Paris", "Prague", "Rome", "Vienna", "Warsaw"], "Oslo"),
    (["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"], "Thursday"),
    (["Triangle", "Square", "Pentagon", "Hexagon", "Heptagon", "Octagon"], "Hexagon"),
    (["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel"], "Delta"),
    (["Bronze", "Copper", "Gold", "Iron", "Nickel", "Silver", "Tin", "Zinc"], "Copper"),
]


def decimals(step):
    """The digits after the point that a slider's step needs: 0 for 5, 1 for 0.1, 2 for 0.05."""
    text = f"{step:g}"
    return len(text.split(".")[1]) if "." in text else 0


def show(value, step):
    """A slider's value as its read-only label shows it, and as the grade compares it."""
    return f"{value:.{decimals(step)}f}"


def range_text(item):
    """What a slider's caption says of its range and step."""
    step = item["step"]
    return f"From {show(item['lowest'], step)} to {show(item['highest'], step)}, in steps of {step:g}."


def join(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _build():
    kinds = [
        [{"kind": "checkbox", "options": o, "answer": sorted(a, key=o.index),
          "question": f"Tick {join(a)}."} for o, a in CHECKBOXES],
        [{"kind": "slider", "lowest": lo, "highest": hi, "step": st, "start": sv, "answer": show(t, st),
          "question": f"Set the slider to {show(t, st)}."} for lo, hi, st, sv, t in SLIDERS],
        [{"kind": "text", "answer": t, "question": "Type this text into the field:"} for t in TEXTS],
        [{"kind": "dropdown", "options": [PLACEHOLDER] + o, "answer": a, "question": f"Choose {a}."}
         for o, a in DROPDOWNS],
    ]
    items = [kind[i] for i in range(len(kinds[0])) for kind in kinds]
    for number, item in enumerate(items, 1):
        item["number"] = number
    return items


ITEMS = _build()
assert len(ITEMS) == MAX_ITEMS


KINDS = ("checkbox", "slider", "text", "dropdown")


def kinds():
    """The kinds of question a run asks: all, or those that $HYPRMASQ_FORM_KINDS lists, separated by commas, such as
    checkbox,slider,text for a questionnaire without dropdowns. The app and the grade read the same variable."""
    listed = [k.strip() for k in os.environ.get("HYPRMASQ_FORM_KINDS", "").split(",") if k.strip()]
    unknown = sorted(set(listed) - set(KINDS))
    if unknown:
        raise ValueError(f"HYPRMASQ_FORM_KINDS names no kind of question: {', '.join(unknown)}")
    return tuple(k for k in KINDS if k in listed) if listed else KINDS


def items(count):
    """The questions of a run of count questions: the first count of the kinds asked, numbered from 1."""
    asked = [item for item in ITEMS if item["kind"] in kinds()]
    if not MIN_ITEMS <= count <= len(asked):
        raise ValueError(f"the questionnaire has {MIN_ITEMS} to {len(asked)} questions, not {count}")
    return [dict(item, number=number) for number, item in enumerate(asked[:count], 1)]


def correct(item, value):
    """Whether value, as form.py records it, answers the item."""
    if item["kind"] == "checkbox":
        return sorted(value or [], key=item["options"].index) == item["answer"]
    if item["kind"] == "text":
        return (value or "").strip() == item["answer"]
    return value == item["answer"]


def initial(item):
    """An item's value before any input."""
    return {"checkbox": [], "slider": show(item.get("start", 0), item.get("step", 1)), "text": "",
            "dropdown": PLACEHOLDER}[item["kind"]]
