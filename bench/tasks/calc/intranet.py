"""Kestrel's intranet, where the offices post their figures: intranet.py STATE-DIR

It listens on intranet.example.test:80. Its pages are shells that render their content in the browser from JSON
under /api/, and both need Mara's session cookie: without it, a page redirects to the sign-in page and the API
answers 401. So the figures reach only a browser signed in as Mara, not Calc's external data or WEBSERVICE().

While STATE-DIR/sign-in-open exists, a request without the cookie is signed in as Mara, as single sign-on would:
setup.py opens Mara's tabs then and removes the file. Every request goes to STATE-DIR/requests.jsonl with the time,
for the grade.
"""

import html
import json
import os
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figures import REVENUE, TABS  # noqa: E402

STATE_DIR = None  # set by main
COOKIE = "kestrel_session"
SESSION = "mara.5c1e9f7a2b4d"
PASSWORD = "Kestrel-Mara-2026"


def euro(value):
    return f"€{value:,}"


def thousands(value):
    return f"{value / 1000:.1f}"


# The pages Mara has open, by path: their title and what their API returns.
def region_north():
    rows = []
    for office in ("Hamburg", "Rotterdam"):
        rows.append({"office": office, "values": [*REVENUE[office], sum(REVENUE[office])]})
    total = [sum(r["values"][i] for r in rows) for i in range(4)]
    return {
        "heading": "Region North: revenue Q4 2025",
        "intro": "Monthly revenue of the offices in Region North after the year-end close, in EUR.",
        "columns": ["Office", "October", "November", "December", "Q4 2025"],
        "rows": rows,
        "total": {"office": "Region North total", "values": total},
        "note": "Figures as of 12 January 2026. Questions to the Region North finance team.",
    }


STOCKHOLM = [
    ("January", 158420, 150000), ("February", 149870, 150000), ("March", 171230, 160000),
    ("April", 168540, 165000), ("May", 175910, 170000), ("June", 181060, 175000),
    ("July", 142380, 150000), ("August", 151640, 150000), ("September", 177820, 170000),
    ("October", REVENUE["Stockholm"][0], 180000), ("November", REVENUE["Stockholm"][1], 185000),
    ("December", REVENUE["Stockholm"][2], 195000),
]
STOCKHOLM_PAGE_SIZE = 6


def stockholm(query):
    page = max(1, min(2, int((query.get("page") or ["1"])[0] or 1)))
    start = (page - 1) * STOCKHOLM_PAGE_SIZE
    rows = [
        {"month": f"{m} 2025", "revenue": r, "budget": b, "vs_budget": f"{(r / b - 1) * 100:+.1f}%"}
        for m, r, b in STOCKHOLM[start:start + STOCKHOLM_PAGE_SIZE]
    ]
    return {
        "heading": "Stockholm: monthly revenue 2025",
        "intro": "Revenue and budget of the Stockholm office per month, in EUR.",
        "rows": rows,
        "page": page,
        "pages": 2,
        "first": start + 1,
        "last": start + len(rows),
        "count": len(STOCKHOLM),
    }


LISBON = [
    ("4 March 2026", "February 2026 figures", 73920,
     "A quieter month after the January campaign. Two new customers in Porto signed in the last week."),
    ("4 February 2026", "January 2026 figures", 76240,
     "The January campaign brought 41 new orders, most of them from returning customers."),
    ("7 January 2026", "December 2025 figures", REVENUE["Lisbon"][2],
     "Our best month of the year, with the year-end orders of the logistics customers."),
    ("9 December 2025", "November 2025 figures, final", REVENUE["Lisbon"][1],
     "This replaces the draft of 2 December, which counted an invoice of €4,750 for December in November."),
    ("2 December 2025", "November 2025 figures, draft", 74600,
     "Draft before the invoice check. The final figure follows next week."),
    ("5 November 2025", "October 2025 figures", REVENUE["Lisbon"][0],
     "Steady month. The new office in Alcântara is ready for the team from mid-November."),
    ("6 October 2025", "September 2025 figures", 67300,
     "Back from the summer with the usual September orders from the schools."),
]
LISBON_FIRST = 4  # shown before "Show older updates"


def lisbon(query):
    older = (query.get("older") or ["0"])[0] == "1"
    items = LISBON[LISBON_FIRST:] if older else LISBON[:LISBON_FIRST]
    return {
        "heading": "Lisbon office: updates",
        "intro": "News and monthly figures from the Lisbon office, newest first.",
        "updates": [{"posted": p, "title": t, "revenue": euro(r), "text": x} for p, t, r, x in items],
        "more": not older,
    }


VIENNA_SECTIONS = [
    ("Summary", [
        "The fourth quarter was the Vienna office's strongest of the year. Revenue grew in every month of the quarter, "
        "by 16.7% from October to December, while operating costs rose more slowly than planned. The team completed "
        "the move of the warehouse planning to the new system and took on two large customers from the retail sector.",
        "This report covers the market, our customers, the main projects, the team, operations and the outlook for "
        "2026. The key figures are at the end of the report.",
    ]),
    ("Market", [
        "Demand in Austria recovered in the autumn after a weak summer. Retail customers ordered earlier than in 2024, "
        "and the holiday season started in the second half of November. Prices stayed stable; two competitors raised "
        "their rates for express deliveries, which brought us several enquiries from their customers.",
        "In the Czech Republic and Slovakia, which we serve from Vienna, the market stayed flat. We expect the new "
        "partner in Brno to help us grow there from the second quarter of 2026.",
    ]),
    ("Customers", [
        "We signed two new retail customers in October and started deliveries for both in November. Of our existing "
        "customers, the three largest renewed their contracts for 2026, one of them for two years. Customer "
        "satisfaction in the December survey was 4.4 out of 5, up from 4.1 in the summer.",
        "Complaints fell by a third compared with the third quarter. Most of the remaining ones concerned delivery "
        "windows in the week before Christmas, when we ran at full capacity.",
    ]),
    ("Projects", [
        "The move of the warehouse planning to the new system finished on 21 November, two weeks later than planned "
        "but within budget. Since then, the planners spend about an hour less a day on manual corrections.",
        "The pilot of electric vans for deliveries in the city centre ran from October to December with four vehicles. "
        "Drivers and customers liked them; we will decide on a larger fleet in March, once the charging costs of a "
        "full winter are known.",
    ]),
    ("Team", [
        "The office had 38 employees at the end of the year, two more than at the end of September. We hired a second "
        "customer service lead and a planner for the new retail customers. Sick leave was low for the season.",
        "Twelve people took the new training on the planning system, and four of them now train others. The team "
        "event in November took us to the Wachau for a day.",
    ]),
    ("Operations", [
        "On-time delivery was 96.8% for the quarter, against a target of 95%. The busiest day was 19 December with "
        "1,240 deliveries. We added a third shift in the warehouse for the last three weeks of the year.",
        "Fuel costs stayed close to plan. Repairs of the older vans were higher than expected, which is another "
        "reason to replace them with electric ones over the next two years.",
    ]),
    ("Risks", [
        "The largest risk for 2026 is our dependence on the three largest customers, who make up almost half of our "
        "revenue. The new retail customers reduce it somewhat. A second risk is the labour market for drivers, where "
        "wages rose faster than our prices.",
        "We have no open legal disputes. The insurance claim for the water damage in the warehouse in August was "
        "settled in December.",
    ]),
    ("Outlook 2026", [
        "We plan for revenue growth of 8 to 10% in 2026, mainly from the new retail customers and the partner in Brno. "
        "The first quarter is usually our weakest, and January started as expected.",
        "Investments in 2026 will go to electric vans, the charging points in the yard and a second loading dock. "
        "We will present the detailed plan at the regional meeting in February.",
    ]),
]


def vienna():
    rows = []
    costs = (96100, 99400, 104800)
    for label, values in (("Revenue", REVENUE["Vienna"]), ("Operating costs", costs),
                          ("Operating result", tuple(r - c for r, c in zip(REVENUE["Vienna"], costs)))):
        rows.append({"label": label, "values": [thousands(v) for v in (*values, sum(values))]})
    return {
        "heading": "Vienna office: quarterly report Q4 2025",
        "meta": "Prepared by the Vienna office management, 15 January 2026",
        "sections": [{"heading": h, "paragraphs": p} for h, p in VIENNA_SECTIONS],
        "figures": {
            "heading": "Key figures",
            "caption": "In EUR thousands",
            "columns": ["", "October", "November", "December", "Q4 2025"],
            "rows": rows,
        },
    }


def dublin():
    oct_, nov, dec = REVENUE["Dublin"]
    return {
        "heading": "Dublin closes 2025 with a record December",
        "meta": "Internal Communications · 14 January 2026",
        "paragraphs": [
            "The Dublin office ended the year on a high. After a slow start to the autumn, the team won back two "
            "customers it had lost in the spring and signed a framework agreement with a pharmaceutical distributor "
            "in Leixlip.",
            f"The office closed October at {euro(oct_)} and November at {euro(nov)}. December then brought "
            f"{euro(dec)}, the first month above €100,000 in the office's history and well ahead of December 2024 "
            "(€88,900).",
            "\"We knew the pharmaceutical contract would make a difference, but not how fast,\" says office lead "
            "Aoife Brennan. \"The team handled twice the usual December volume without a single missed delivery.\"",
            "For 2026, Dublin plans to add two drivers and a second van for the routes to the west. The office will "
            "also host the regional meeting in April.",
        ],
    }


# Each tab's page: its kind of layout, which the script in the page renders, and its API's answer.
LAYOUTS = [
    ("table", lambda q: region_north()),
    ("pages", stockholm),
    ("feed", lisbon),
    ("report", lambda q: vienna()),
    ("article", lambda q: dublin()),
]
PAGES = {path: (title, kind, api) for (path, title), (kind, api) in zip(TABS, LAYOUTS)}

STYLE = """
body { margin: 0; font-family: "DejaVu Sans", sans-serif; font-size: 16px; line-height: 1.5; color: #1f2933;
       background: #f4f6f8; }
header.top { background: #234e70; color: #fff; padding: 10px 24px; display: flex; justify-content: space-between; }
header.top .brand { font-weight: bold; }
main { max-width: 900px; margin: 0 auto; padding: 16px 24px 48px; }
.crumbs { color: #616e7c; font-size: 14px; margin: 0 0 8px; }
h1 { font-size: 28px; line-height: 1.25; margin: 0 0 8px; }
h2 { font-size: 21px; margin: 28px 0 8px; }
.meta { color: #616e7c; font-size: 14px; }
.panel { background: #fff; border: 1px solid #d9e2ec; border-radius: 6px; padding: 16px 20px; margin-top: 16px; }
table { border-collapse: collapse; font-size: 15px; margin: 8px 0; }
th, td { padding: 6px 14px; border-bottom: 1px solid #d9e2ec; text-align: right; }
th:first-child, td:first-child { text-align: left; }
thead th { background: #e4ebf2; }
tr.total td { font-weight: bold; border-top: 2px solid #9fb3c8; }
caption { text-align: left; color: #616e7c; font-size: 14px; padding-bottom: 4px; }
.pager { display: flex; gap: 8px; align-items: center; margin-top: 12px; font-size: 15px; }
.pager button { font-size: 15px; padding: 4px 12px; }
.pager button.current { font-weight: bold; background: #234e70; color: #fff; border: 1px solid #234e70; }
.card { background: #fff; border: 1px solid #d9e2ec; border-radius: 6px; padding: 14px 20px; margin-top: 14px; }
.card .posted { color: #616e7c; font-size: 14px; }
.card h2 { margin: 2px 0 4px; font-size: 19px; }
.card .figure { font-size: 24px; font-weight: bold; }
button.more { margin-top: 18px; font-size: 16px; padding: 8px 18px; }
.notice { background: #fff3c4; border: 1px solid #f0c674; padding: 12px 16px; border-radius: 6px; }
"""

SCRIPT = """
const app = document.getElementById("app");
const el = (tag, attrs, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  for (const c of children) node.append(c);
  return node;
};
const fmt = (n) => n.toLocaleString("en-GB");
async function load(query) {
  const response = await fetch(PAGE.api + (query || ""), { credentials: "same-origin" });
  if (response.status === 401) {
    app.replaceChildren(el("p", { class: "notice" }, "Your session has ended. ",
      el("a", { href: "/login?next=" + encodeURIComponent(location.pathname) }, "Sign in again"), "."));
    throw new Error("signed out");
  }
  return response.json();
}
function header(data, metaKey) {
  const nodes = [el("h1", {}, data.heading)];
  if (data[metaKey]) nodes.push(el("p", { class: "meta" }, data[metaKey]));
  return nodes;
}
function table(columns, rows, caption) {
  const t = el("table", {});
  if (caption) t.append(el("caption", {}, caption));
  t.append(el("thead", {}, el("tr", {}, ...columns.map((c) => el("th", {}, c)))));
  const body = el("tbody", {});
  for (const r of rows) body.append(el("tr", r.cls ? { class: r.cls } : {}, ...r.cells.map((c) => el("td", {}, c))));
  t.append(body);
  return t;
}
const render = {
  async table() {
    const d = await load();
    const rows = d.rows.map((r) => ({ cells: [r.office, ...r.values.map(fmt)] }));
    rows.push({ cls: "total", cells: [d.total.office, ...d.total.values.map(fmt)] });
    app.replaceChildren(...header(d, "intro"),
      el("div", { class: "panel" }, table(d.columns, rows), el("p", { class: "meta" }, d.note)));
  },
  async pages() {
    const panel = el("div", { class: "panel" });
    let first = true;
    async function show(page) {
      const d = await load("?page=" + page);
      if (first) { app.replaceChildren(...header(d, "intro"), panel); first = false; }
      const rows = d.rows.map((r) => ({ cells: [r.month, fmt(r.revenue), fmt(r.budget), r.vs_budget] }));
      const pager = el("div", { class: "pager" }, el("span", {}, `Showing ${d.first}–${d.last} of ${d.count}`));
      const button = (label, target, current) => {
        const b = el("button", current ? { class: "current" } : {}, label);
        if (target < 1 || target > d.pages) b.disabled = true;
        else b.addEventListener("click", () => show(target));
        return b;
      };
      pager.append(button("‹ Previous", d.page - 1));
      for (let p = 1; p <= d.pages; p++) pager.append(button(String(p), p, p === d.page));
      pager.append(button("Next ›", d.page + 1));
      panel.replaceChildren(table(["Month", "Revenue", "Budget", "vs. budget"], rows), pager);
    }
    await show(1);
  },
  async feed() {
    const d = await load();
    const list = el("div", {});
    const add = (updates) => {
      for (const u of updates) list.append(el("div", { class: "card" },
        el("div", { class: "posted" }, "Posted " + u.posted), el("h2", {}, u.title),
        el("div", { class: "figure" }, "Revenue " + u.revenue), el("p", {}, u.text)));
    };
    add(d.updates);
    const nodes = [...header(d, "intro"), list];
    if (d.more) {
      const more = el("button", { class: "more" }, "Show older updates");
      more.addEventListener("click", async () => {
        more.disabled = true;
        const older = await load("?older=1");
        add(older.updates);
        more.remove();
      });
      nodes.push(more);
    }
    app.replaceChildren(...nodes);
  },
  async report() {
    const d = await load();
    const nodes = header(d, "meta");
    for (const s of d.sections) nodes.push(el("h2", {}, s.heading), ...s.paragraphs.map((p) => el("p", {}, p)));
    const f = d.figures;
    nodes.push(el("h2", {}, f.heading), table(f.columns, f.rows.map((r) => ({ cells: [r.label, ...r.values] })), f.caption));
    app.replaceChildren(...nodes);
  },
  async article() {
    const d = await load();
    app.replaceChildren(...header(d, "meta"), ...d.paragraphs.map((p) => el("p", {}, p)));
  },
};
render[PAGE.kind]().catch((e) => console.log(e));
"""


def shell(title, body, script="", user="Mara Lindqvist"):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>{STYLE}</style></head>
<body>
<header class="top"><span class="brand">Kestrel intranet</span><span>{user}</span></header>
<main>{body}</main>
{script}
</body></html>
"""


def page_html(path, title, kind):
    crumbs = " › ".join(html.escape(p.replace("-", " ")) for p in path.strip("/").split("/")[:-1])
    config = json.dumps({"kind": kind, "api": "/api" + path})
    return shell(title, f'<p class="crumbs">{crumbs}</p><div id="app"><p>Loading…</p></div>',
                 f"<script>const PAGE = {config};\n{SCRIPT}</script>")


def home_html():
    links = "".join(f'<li><a href="{p}">{html.escape(t)}</a></li>' for p, (t, _, _) in PAGES.items())
    return shell("Kestrel intranet", f"<h1>Reports and news</h1><ul>{links}</ul>")


LOGIN = """<h1>Sign in</h1>
<form method="post" action="/login" class="panel">
<input type="hidden" name="next" value="{next}">
<p><label>User name<br><input name="user" autocomplete="username"></label></p>
<p><label>Password<br><input name="password" type="password" autocomplete="current-password"></label></p>
<p><button type="submit">Sign in</button></p>{error}
</form>"""


class Handler(BaseHTTPRequestHandler):
    def signed_in(self):
        """Whether the request carries Mara's session, and whether this response has to set it."""
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and value == SESSION:
                return True, False
        if os.path.exists(os.path.join(STATE_DIR, "sign-in-open")):
            return True, True
        return False, False

    def send(self, status, body, content_type="text/html; charset=utf-8", headers=()):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)
        self.log_request_line(status)

    def log_request_line(self, status):
        url = urllib.parse.urlsplit(self.path)
        with open(os.path.join(STATE_DIR, "requests.jsonl"), "a") as f:
            f.write(json.dumps({
                "time": time.time(), "method": self.command, "path": url.path, "query": url.query,
                "status": status, "signed_in": self.session[0], "agent": self.headers.get("User-Agent", ""),
            }) + "\n")

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        self.session = self.signed_in()
        signed_in, set_cookie = self.session
        cookie = [("Set-Cookie", f"{COOKIE}={SESSION}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")] \
            if set_cookie else []
        if url.path == "/login":
            target = (query.get("next") or ["/"])[0]
            self.send(200, shell("Sign in · Kestrel intranet", LOGIN.format(next=html.escape(target), error=""), user=""))
        elif url.path.startswith("/api/"):
            page = PAGES.get(url.path[len("/api"):])
            if not signed_in:
                self.send(401, json.dumps({"error": "not signed in"}), "application/json")
            elif page is None:
                self.send(404, json.dumps({"error": "not found"}), "application/json")
            else:
                self.send(200, json.dumps(page[2](query)), "application/json", cookie)
        elif not signed_in:
            target = urllib.parse.quote(url.path)
            self.send(302, "", headers=[("Location", f"/login?next={target}")])
        elif url.path == "/":
            self.send(200, home_html(), headers=cookie)
        elif url.path in PAGES:
            title, kind, _ = PAGES[url.path]
            self.send(200, page_html(url.path, title, kind), headers=cookie)
        else:
            self.send(404, shell("Not found · Kestrel intranet", "<h1>Not found</h1>"))

    def do_POST(self):
        self.session = (False, False)
        if urllib.parse.urlsplit(self.path).path != "/login":
            self.send(404, "Not found", "text/plain")
            return
        form = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
        target = (form.get("next") or ["/"])[0]
        if not target.startswith("/"):
            target = "/"
        if (form.get("user") or [""])[0] == "mara" and (form.get("password") or [""])[0] == PASSWORD:
            self.send(303, "", headers=[
                ("Location", target),
                ("Set-Cookie", f"{COOKIE}={SESSION}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"),
            ])
        else:
            error = '<p class="notice">Wrong user name or password.</p>'
            self.send(200, shell("Sign in · Kestrel intranet", LOGIN.format(next=html.escape(target), error=error),
                                 user=""))

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    STATE_DIR = sys.argv[1]
    os.makedirs(STATE_DIR, exist_ok=True)
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PORT", "80"))), Handler).serve_forever()
