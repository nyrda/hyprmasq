"""Mara's blog, where she marks up the figures of a post: figure.py STATE-DIR TARGET-DIR

It listens on blog.example.test:80 and serves one figure, the photo of the variant in TARGET-DIR (photo, track.json
and figure.json, which names the page and the target), with a brush to draw on it, as a figure editor of a blog would. The page draws with pointer events only, as a canvas editor does:

- a drag on the photo draws a stroke along the pointer's path, from every pointer position the browser reports:
  one a frame, and the ones it coalesced between frames where it gives them, which Firefox does only to a secure
  context, and the blog is plain http;
- a click draws a dot;
- Shift and a click draws a straight line from where the last stroke ended to the click;
- Undo (or Ctrl+Z) takes back the last stroke, and Clear removes them all.

A stroke's points are in photo pixels. The page saves the whole drawing after every change: STATE-DIR/strokes.json
holds the strokes, which the grade reads, and every save goes to STATE-DIR/saves.jsonl with the time, the change and
the strokes. The page also logs every pointer and key event it gets, its layout, and each stroke it draws, with its
points in client and photo pixels, to STATE-DIR/events.jsonl, for attribute.py.
"""

import html
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TARGET = TRACK = FIGURE = None  # set by main
WIDTH = HEIGHT = 0
PAGE_PATH = PHOTO_PATH = None
# The brush's width in photo pixels.
BRUSH = 3
CHANGES = ("stroke", "undo", "clear")
# The most strokes and points a drawing keeps, far beyond what the task needs.
MAX_STROKES = 2000
MAX_POINTS = 20000

STATE_DIR = None  # set by main
LOCK = threading.Lock()

STYLE = """
html, body { margin: 0; height: 100%; overflow: hidden; }
body { font-family: "DejaVu Sans", sans-serif; font-size: 15px; color: #1f2933; background: #eef1f4;
       user-select: none; -moz-user-select: none; }
header { height: 44px; box-sizing: border-box; display: flex; align-items: center; justify-content: space-between;
         padding: 0 16px; background: #34423a; color: #fff; }
header .crumbs { opacity: 0.8; }
header h1 { font-size: 17px; margin: 0; font-weight: bold; }
header .status { font-size: 14px; opacity: 0.9; }
main { display: flex; gap: 16px; padding: 16px; align-items: flex-start; }
#figure { position: relative; flex: none; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3); background: #fff; }
#photo { display: block; width: 100%; height: 100%; }
#ink { position: absolute; left: 0; top: 0; width: 100%; height: 100%; cursor: crosshair; touch-action: none; }
#panel { width: 240px; flex: none; box-sizing: border-box; background: #fff; border: 1px solid #c8d1db;
         border-radius: 6px; padding: 10px 12px; }
#panel h2 { font-size: 16px; margin: 0 0 8px; }
#panel p { font-size: 13px; color: #52606d; margin: 0 0 10px; line-height: 1.35; }
.tools { display: flex; gap: 8px; margin-bottom: 12px; }
button { font: inherit; font-size: 14px; padding: 4px 12px; border: 1px solid #9aa5b1; border-radius: 4px;
         background: #f5f7fa; color: #1f2933; }
button:disabled { color: #9aa5b1; }
.brush { display: flex; align-items: center; gap: 8px; font-size: 14px; margin-bottom: 12px; }
.swatch { width: 18px; height: 18px; border-radius: 50%; background: #ff2d55; border: 1px solid #1f2933; }
"""

SCRIPT = """
const PHOTO = { width: CONFIG.width, height: CONFIG.height };
const figure = document.getElementById("figure");
const photo = document.getElementById("photo");
const ink = document.getElementById("ink");
const panel = document.getElementById("panel");
const status = document.getElementById("status");
const undoButton = document.getElementById("undo");
const clearButton = document.getElementById("clear");
let strokes = [];
// The stroke being drawn, while the button is down, and the pointer drawing it.
let current = null, drawing = null;

// The page's log of pointer and key events, its layout and its strokes, sent to the server in batches.
const queue = [];
const now = () => Math.round((performance.timeOrigin + performance.now()) * 10) / 10;
function log(entry) { queue.push({ t: now(), ...entry }); }
function flush() {
  if (!queue.length) return;
  const batch = queue.splice(0);
  fetch("/api/events", { method: "POST", body: JSON.stringify(batch), keepalive: true,
                         headers: { "Content-Type": "application/json" } }).catch(() => {});
}
setInterval(flush, 250);
addEventListener("pagehide", flush);

const inside = (r, x, y) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
function where(x, y) {
  if (inside(photo.getBoundingClientRect(), x, y)) return "photo";
  if (inside(panel.getBoundingClientRect(), x, y)) return "panel";
  return "page";
}
const round1 = (v) => Math.round(v * 10) / 10;
const rect = (r) => [r.left, r.top, r.right, r.bottom].map(round1);

for (const type of ["pointerdown", "pointermove", "pointerup", "pointercancel", "lostpointercapture", "click",
                    "dragstart"]) {
  addEventListener(type, (e) => {
    const entry = { type, x: e.clientX, y: e.clientY, buttons: e.buttons, button: e.button, shift: e.shiftKey,
                    over: where(e.clientX, e.clientY) };
    if (type === "pointermove" && e.getCoalescedEvents) entry.coalesced = e.getCoalescedEvents().length;
    log(entry);
  }, { capture: true, passive: true });
}
addEventListener("keydown", (e) => log({ type: "keydown", key: e.key, ctrl: e.ctrlKey, shift: e.shiftKey }),
                 { capture: true, passive: true });

function scale() { return figure.clientWidth / PHOTO.width; }

function layout() {
  const header = document.querySelector("header").offsetHeight;
  const availW = innerWidth - panel.offsetWidth - 3 * 16;
  const availH = innerHeight - header - 2 * 16;
  const s = Math.min(availW / PHOTO.width, availH / PHOTO.height);
  figure.style.width = Math.floor(PHOTO.width * s) + "px";
  figure.style.height = Math.floor(PHOTO.height * s) + "px";
  ink.width = Math.round(figure.clientWidth * devicePixelRatio);
  ink.height = Math.round(figure.clientHeight * devicePixelRatio);
  render();
  log({ type: "layout", inner: [innerWidth, innerHeight], dpr: devicePixelRatio,
        screen: [window.mozInnerScreenX, window.mozInnerScreenY], scale: scale(),
        photo: rect(photo.getBoundingClientRect()), panel: rect(panel.getBoundingClientRect()),
        buttons: { undo: rect(undoButton.getBoundingClientRect()), clear: rect(clearButton.getBoundingClientRect()) },
        strokes: strokes.length });
}

function render() {
  const ctx = ink.getContext("2d");
  const k = scale() * devicePixelRatio;
  ctx.clearRect(0, 0, ink.width, ink.height);
  ctx.strokeStyle = ctx.fillStyle = "#ff2d55";
  ctx.lineWidth = CONFIG.brush * k;
  ctx.lineCap = ctx.lineJoin = "round";
  for (const stroke of [...strokes, ...(current ? [current] : [])]) {
    const p = stroke.points;
    ctx.beginPath();
    if (p.length === 1) {
      ctx.arc(p[0][0] * k, p[0][1] * k, ctx.lineWidth / 2, 0, 2 * Math.PI);
      ctx.fill();
      continue;
    }
    ctx.moveTo(p[0][0] * k, p[0][1] * k);
    for (const q of p.slice(1)) ctx.lineTo(q[0] * k, q[1] * k);
    ctx.stroke();
  }
  undoButton.disabled = clearButton.disabled = !strokes.length;
}

// Saves are sent one after another, in the order of the changes.
let saving = Promise.resolve();
function save(change) {
  status.textContent = "Saving…";
  const body = JSON.stringify({ change, strokes });
  saving = saving.then(() => fetch("/api/strokes", { method: "POST", headers: { "Content-Type": "application/json" },
                                                      body }))
    .then((r) => { status.textContent = r.ok ? "All changes saved" : "Not saved"; })
    .catch(() => { status.textContent = "Not saved"; });
  flush();
}

function toPhoto(x, y) {
  const r = photo.getBoundingClientRect();
  const s = r.width / PHOTO.width;
  const clamp = (v, max) => Math.min(max, Math.max(0, round1(v)));
  return [clamp((x - r.left) / s, PHOTO.width), clamp((y - r.top) / s, PHOTO.height)];
}

function finish(stroke, from, to) {
  strokes = [...strokes, stroke];
  current = null;
  render();
  log({ type: "stroke", kind: stroke.kind, from, to, points: stroke.points });
  save("stroke");
}

addEventListener("dragstart", (e) => e.preventDefault());
ink.addEventListener("pointerdown", (e) => {
  if (e.button !== 0 || drawing) return;
  e.preventDefault();
  const point = toPhoto(e.clientX, e.clientY);
  const last = strokes.length ? strokes[strokes.length - 1].points.at(-1) : null;
  if (e.shiftKey && last) {
    finish({ kind: "line", points: [last, point] }, [e.clientX, e.clientY], [e.clientX, e.clientY]);
    return;
  }
  ink.setPointerCapture(e.pointerId);
  drawing = { id: e.pointerId, from: [e.clientX, e.clientY] };
  current = { kind: "brush", points: [point] };
  render();
});
ink.addEventListener("pointermove", (e) => {
  if (!drawing || e.pointerId !== drawing.id) return;
  const events = e.getCoalescedEvents ? e.getCoalescedEvents() : [];
  for (const c of events.length ? events : [e]) {
    const point = toPhoto(c.clientX, c.clientY);
    const last = current.points.at(-1);
    if (point[0] !== last[0] || point[1] !== last[1]) current.points.push(point);
  }
  render();
});
ink.addEventListener("pointerup", (e) => {
  if (!drawing || e.pointerId !== drawing.id) return;
  const point = toPhoto(e.clientX, e.clientY);
  const last = current.points.at(-1);
  if (point[0] !== last[0] || point[1] !== last[1]) current.points.push(point);
  const from = drawing.from;
  drawing = null;
  finish(current, from, [e.clientX, e.clientY]);
});
for (const type of ["pointercancel", "lostpointercapture"]) {
  ink.addEventListener(type, (e) => {
    if (drawing && e.pointerId === drawing.id) { drawing = null; current = null; render(); }
  });
}

function undo() {
  if (!strokes.length) return;
  strokes = strokes.slice(0, -1);
  render();
  log({ type: "undo", strokes: strokes.length });
  save("undo");
}
undoButton.addEventListener("click", undo);
clearButton.addEventListener("click", () => {
  if (!strokes.length) return;
  strokes = [];
  render();
  log({ type: "clear" });
  save("clear");
});
addEventListener("keydown", (e) => {
  if (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === "z") { e.preventDefault(); undo(); }
});

addEventListener("resize", layout);
fetch("/api/strokes").then((r) => r.json()).then((saved) => {
  strokes = saved.strokes;
  if (photo.complete) layout(); else photo.addEventListener("load", layout);
});
"""


def page_html():
    config = json.dumps({"width": WIDTH, "height": HEIGHT, "brush": BRUSH})
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(FIGURE["title"])}</title>
<style>{STYLE}</style></head>
<body>
<header><span><span class="crumbs">{html.escape(FIGURE["crumbs"])}</span>
<h1 style="display:inline">{html.escape(FIGURE["heading"])}</h1></span>
<span class="status" id="status">All changes saved</span></header>
<main>
<div id="figure" style="width:{WIDTH * 2}px;height:{HEIGHT * 2}px"><img id="photo" src="{PHOTO_PATH}"
 alt="{html.escape(FIGURE["alt"])}" draggable="false"><canvas id="ink"></canvas></div>
<aside id="panel"><h2>Draw on the figure</h2>
<div class="brush"><span class="swatch"></span> Brush</div>
<div class="tools"><button id="undo" title="Undo (Ctrl+Z)">Undo</button><button id="clear">Clear</button></div>
<p>Drag on the photo to draw. Click to draw a dot. Shift+click draws a straight line from where the last stroke
ended.</p></aside>
</main>
<script>const CONFIG = {config};
{SCRIPT}</script>
</body></html>
"""


def load_state():
    path = os.path.join(STATE_DIR, "strokes.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"strokes": []}


def save_state(state):
    tmp = os.path.join(STATE_DIR, "strokes.json.tmp")
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, os.path.join(STATE_DIR, "strokes.json"))


def number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def valid_strokes(strokes):
    """Whether strokes is a drawing the page could have made: strokes of points inside the photo."""
    if not isinstance(strokes, list) or len(strokes) > MAX_STROKES:
        return False
    points = 0
    for stroke in strokes:
        if not isinstance(stroke, dict) or stroke.get("kind") not in ("brush", "line"):
            return False
        p = stroke.get("points")
        if not isinstance(p, list) or not p:
            return False
        points += len(p)
        if not all(isinstance(q, list) and len(q) == 2 and all(number(v) for v in q)
                   and 0 <= q[0] <= WIDTH and 0 <= q[1] <= HEIGHT for q in p):
            return False
    return points <= MAX_POINTS


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type="application/json", headers=()):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            self.send(302, "", headers=[("Location", PAGE_PATH)])
        elif path == PAGE_PATH:
            self.send(200, page_html(), "text/html; charset=utf-8")
        elif path == PHOTO_PATH:
            with open(os.path.join(TARGET, TRACK["image"]), "rb") as f:
                self.send(200, f.read(), "image/png" if TRACK["image"].endswith(".png") else "image/jpeg")
        elif path == "/api/strokes":
            with LOCK:
                self.send(200, json.dumps(load_state()))
        else:
            self.send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            body = self.body()
        except ValueError:
            self.send(400, json.dumps({"error": "not JSON"}))
            return
        if path == "/api/events" and isinstance(body, list):
            received = time.time()
            with LOCK, open(os.path.join(STATE_DIR, "events.jsonl"), "a") as f:
                for entry in body:
                    f.write(json.dumps({"received": received, **entry}) + "\n")
            self.send(204, "")
        elif path == "/api/strokes" and isinstance(body, dict):
            change, strokes = body.get("change"), body.get("strokes")
            if change not in CHANGES or not valid_strokes(strokes):
                self.send(400, json.dumps({"error": "no such change or drawing"}))
                return
            with LOCK:
                save_state({"strokes": strokes})
                with open(os.path.join(STATE_DIR, "saves.jsonl"), "a") as f:
                    f.write(json.dumps({"time": time.time(), "change": change, "strokes": strokes}) + "\n")
                self.send(200, json.dumps({"strokes": len(strokes)}))
        else:
            self.send(404, json.dumps({"error": "not found"}))

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    STATE_DIR, TARGET = sys.argv[1], sys.argv[2]
    TRACK = json.load(open(os.path.join(TARGET, "track.json")))
    FIGURE = json.load(open(os.path.join(TARGET, "figure.json")))
    WIDTH, HEIGHT = TRACK["size"]
    PAGE_PATH = FIGURE["path"]
    PHOTO_PATH = PAGE_PATH.rsplit("/", 1)[0] + "/" + TRACK["image"]
    os.makedirs(STATE_DIR, exist_ok=True)
    save_state(load_state())
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PORT", "80"))), Handler).serve_forever()
