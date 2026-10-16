"""The figures of the Calc task, for the intranet's pages (intranet.py), Mara's sheet (sheet.py) and the grade
(check.py). Revenue in EUR of each office for October to December 2025, as the sheet should hold it.
"""

# The offices in the order of Mara's sheet, one row each.
OFFICES = ["Dublin", "Hamburg", "Lisbon", "Rotterdam", "Stockholm", "Vienna"]
MONTHS = ["October", "November", "December"]

REVENUE = {
    "Dublin": (84200, 91750, 103400),
    "Hamburg": (156320, 162870, 171540),
    "Lisbon": (71180, 69850, 82410),
    "Rotterdam": (128940, 133260, 139780),
    "Stockholm": (186240, 179650, 201930),
    "Vienna": (112400, 118900, 131200),
}

# The traps of the pages, by office and month: the figure a careless reading enters instead, and why.
TRAPS = {
    ("Lisbon", "November"): (74600, "the draft of 2 December, which the final figure replaces"),
    ("Vienna", "October"): (112.4, "in EUR thousands as the report gives it"),
    ("Vienna", "November"): (118.9, "in EUR thousands as the report gives it"),
    ("Vienna", "December"): (131.2, "in EUR thousands as the report gives it"),
}

# Mara's tabs in Firefox, in their order: the intranet's pages, by path, and their titles.
INTRANET = "http://intranet.example.test"
TABS = [
    ("/reports/region-north/2025-q4", "Region North: revenue Q4 2025"),
    ("/offices/stockholm/revenue/2025", "Stockholm: monthly revenue 2025"),
    ("/offices/lisbon/updates", "Lisbon office: updates"),
    ("/reports/vienna/2025-q4", "Vienna office: quarterly report Q4 2025"),
    ("/news/2026/01/dublin-record-december", "Dublin closes 2025 with a record December"),
]

# Mara's sheet: where the figures go, and the cells that should hold formulas.
SHEET_NAME = "Q4 2025"
FILE_NAME = "Office revenue Q4 2025.ods"
TITLE = "Office revenue Q4 2025"
NOTE = "All figures in EUR, from the offices' reports."
FIRST_ROW = 5  # Dublin; the header is row 4
TOTAL_ROW = FIRST_ROW + len(OFFICES)  # "All offices"
AVERAGE_CELL = f"B{TOTAL_ROW + 3}"
GROWTH_CELL = f"B{TOTAL_ROW + 4}"
AVERAGE_LABEL = "Average revenue per office and month"
GROWTH_LABEL = "Growth from October to December, all offices"
