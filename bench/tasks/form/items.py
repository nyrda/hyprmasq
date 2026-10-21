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


def items(count):
    """The questions of a run of count questions."""
    if not MIN_ITEMS <= count <= MAX_ITEMS:
        raise ValueError(f"the questionnaire has {MIN_ITEMS} to {MAX_ITEMS} questions, not {count}")
    return ITEMS[:count]


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
