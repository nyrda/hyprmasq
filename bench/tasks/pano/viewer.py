"""Mara's blog, where she sets the opening view of a post's panorama: viewer.py STATE-DIR TARGET-DIR

It listens on blog.example.test:80 and serves one panorama, the photo of the variant in TARGET-DIR (photo and
view.json, which names the page, the view it starts at and the target), in a viewer that shows FOV degrees of it and
wraps around. The viewer turns only while a key is held, as a panorama viewer or a game does:

- Left or Right, held, turns the view at SPEED degrees a second, and with Shift held too at FAST;
- S, held for SAVE_MS, saves the view as the post's opening view. Let go sooner, it saves nothing.

The view turns by the time between a key's press and its release, as the events' own timestamps tell it, not by the
frames the page draws or by the key's autorepeat, which it ignores: a hidden window that draws slowly turns as far as
a visible one, and a key that is pressed and released at once turns the view by next to nothing. Firefox on Wayland
gives a held key's repeats as presses that it does not mark as repeats, so a press of a key that is down is one. A scale above the
photo names the directions, 0° at the photo's left edge, and a line marks the view's centre, which is what is saved.

STATE-DIR/state.json holds the view and the saved opening view, which the grade reads. Every view that a turn ended
at goes to STATE-DIR/views.jsonl and every save to STATE-DIR/saves.jsonl, with the time. The page also logs every key
event it gets, its layout, each turn with how long its key was held, and each hold of S, to STATE-DIR/events.jsonl,
for attribute.py.
"""

import html
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TARGET = VIEW = None  # set by main
WIDTH = HEIGHT = 0
PAGE_PATH = PHOTO_PATH = None
# The degrees of the panorama that the viewer shows.
FOV = 100
# Degrees a second that a held arrow key turns the view, and with Shift.
SPEED = 30
FAST = 90
# How long S is held to save the view, in milliseconds.
SAVE_MS = 2000

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
#viewer { flex: none; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3); background: #fff; }
#scale { display: block; height: 30px; background: #1f2933; }
#view { position: relative; overflow: hidden; background-repeat: repeat-x; background-color: #9db4c8; }
#centre { position: absolute; left: 50%; top: 0; bottom: 0; width: 0; border-left: 1px solid rgba(255, 255, 255, 0.75);
          box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.25); }
#panel { width: 260px; flex: none; box-sizing: border-box; background: #fff; border: 1px solid #c8d1db;
         border-radius: 6px; padding: 10px 12px; }
#panel h2 { font-size: 16px; margin: 0 0 8px; }
#panel p { font-size: 13px; color: #52606d; margin: 0 0 10px; line-height: 1.35; }
kbd { font: inherit; font-size: 12px; padding: 1px 5px; border: 1px solid #9aa5b1; border-radius: 3px;
      background: #f5f7fa; color: #1f2933; }
#meter { height: 10px; border: 1px solid #9aa5b1; border-radius: 5px; background: #f5f7fa; overflow: hidden;
         margin-bottom: 6px; }
#fill { height: 100%; width: 0; background: #2f855a; }
#saved { font-size: 14px; margin: 0; color: #1f2933; }
"""

SCRIPT = """
const view = document.getElementById("view");
const viewer = document.getElementById("viewer");
const scale = document.getElementById("scale");
const panel = document.getElementById("panel");
const status = document.getElementById("status");
const fill = document.getElementById("fill");
const savedLine = document.getElementById("saved");
const mod = (v) => ((v % 360) + 360) % 360;
const round1 = (v) => Math.round(v * 10) / 10;
const round3 = (v) => Math.round(v * 1000) / 1000;
// The direction at the view's centre, in degrees, and the opening view that is saved, or null.
let heading = CONFIG.start, saved = null;
// The keys that are down, and the time up to which the view has turned by them, in the time of the key events.
const down = { left: false, right: false, shift: false };
let since = null;
// The turn that a held arrow key is making, and the hold of S.
let turn = null, hold = null;
// The keys that are down, by their place on the keyboard: a press of one of them is the key's autorepeat.
const pressed = new Set();
let stripWidth = 0, viewWidth = 0;

// The page's log of key events, its layout, its turns and its saves, sent to the server in batches.
const queue = [];
const wall = (t) => Math.round((performance.timeOrigin + t) * 10) / 10;
function log(entry, t = performance.now()) { queue.push({ t: wall(t), ...entry }); }
function flush() {
  if (!queue.length) return;
  const batch = queue.splice(0);
  fetch("/api/events", { method: "POST", body: JSON.stringify(batch), keepalive: true,
                         headers: { "Content-Type": "application/json" } }).catch(() => {});
}
setInterval(flush, 250);
addEventListener("pagehide", flush);

function rate() {
  return ((down.right ? 1 : 0) - (down.left ? 1 : 0)) * (down.shift ? CONFIG.fast : CONFIG.speed);
}
// Turns the view up to the time t of a key event. Time never runs backwards between events.
function advance(t) {
  if (since !== null && t > since) heading = mod(heading + rate() * (t - since) / 1000);
  if (since === null || t > since) since = t;
}
// The view as it is now, between key events, for drawing.
function shown() {
  const now = performance.now();
  return since !== null && now > since ? mod(heading + rate() * (now - since) / 1000) : heading;
}

function layout() {
  const header = document.querySelector("header").offsetHeight;
  const availW = innerWidth - panel.offsetWidth - 3 * 16;
  const availH = innerHeight - header - 2 * 16 - scale.offsetHeight;
  viewWidth = Math.floor(availW);
  stripWidth = viewWidth * 360 / CONFIG.fov;
  let height = stripWidth * CONFIG.height / CONFIG.width;
  if (height > availH) {
    height = availH;
    stripWidth = height * CONFIG.width / CONFIG.height;
    viewWidth = Math.floor(stripWidth * CONFIG.fov / 360);
  }
  view.style.width = viewer.style.width = viewWidth + "px";
  view.style.height = Math.floor(height) + "px";
  view.style.backgroundSize = stripWidth + "px " + Math.floor(height) + "px";
  scale.style.width = viewWidth + "px";
  scale.width = Math.round(viewWidth * devicePixelRatio);
  scale.height = Math.round(scale.offsetHeight * devicePixelRatio);
  draw();
  const rect = (r) => [r.left, r.top, r.right, r.bottom].map(round1);
  log({ type: "layout", inner: [innerWidth, innerHeight], dpr: devicePixelRatio, strip: round1(stripWidth),
        view: rect(view.getBoundingClientRect()), heading: round3(heading) });
}

function draw() {
  const h = shown();
  view.style.backgroundPositionX = (viewWidth / 2 - h / 360 * stripWidth) + "px";
  const ctx = scale.getContext("2d");
  const k = devicePixelRatio, perDegree = stripWidth / 360;
  ctx.setTransform(k, 0, 0, k, 0, 0);
  ctx.clearRect(0, 0, viewWidth, 30);
  ctx.fillStyle = ctx.strokeStyle = "#e4e7eb";
  ctx.font = "12px 'DejaVu Sans', sans-serif";
  ctx.textAlign = "center";
  ctx.lineWidth = 1;
  const half = CONFIG.fov / 2;
  for (let d = Math.ceil(h - half); d <= h + half; d++) {
    if (d % 5) continue;
    const x = Math.round(viewWidth / 2 + (d - h) * perDegree) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, d % 10 ? 24 : 18);
    ctx.lineTo(x, 30);
    ctx.stroke();
    if (d % 10 === 0) ctx.fillText(mod(d) + "\\u00b0", x, 14);
  }
  if (saved !== null) {
    // The saved opening view, where it lies in what the viewer shows.
    let d = saved - h;
    d = d > 180 ? d - 360 : d < -180 ? d + 360 : d;
    if (Math.abs(d) <= half) {
      const x = viewWidth / 2 + d * perDegree;
      ctx.fillStyle = "#68d391";
      ctx.beginPath();
      ctx.moveTo(x - 5, 21); ctx.lineTo(x + 5, 21); ctx.lineTo(x, 29);
      ctx.fill();
    }
  }
  // The view's centre, below the scale's numbers: an outline, which shows a saved view that lies there inside it.
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(viewWidth / 2 - 8, 18.5); ctx.lineTo(viewWidth / 2 + 8, 18.5); ctx.lineTo(viewWidth / 2, 30);
  ctx.closePath();
  ctx.stroke();
  if (hold) fill.style.width = Math.min(100, (performance.now() - hold.at) / CONFIG.saveMs * 100) + "%";
}

function frame() {
  draw();
  if (rate() || hold) requestAnimationFrame(frame);
}

function showSaved() {
  savedLine.textContent = saved === null ? "No opening view saved yet." : "Opening view saved.";
}

function post(path, body) {
  return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

// Saves are sent one after another, in the order they were made.
let saving = Promise.resolve();
function save(heldMs) {
  advance(performance.now());
  saved = heading;
  hold.done = true;
  fill.style.width = "100%";
  showSaved();
  draw();
  status.textContent = "Saving…";
  log({ type: "save", heading: round3(saved), held_ms: round1(heldMs) });
  const body = { heading: round3(saved) };
  saving = saving.then(() => post("/api/save", body))
    .then((r) => { status.textContent = r.ok ? "All changes saved" : "Not saved"; })
    .catch(() => { status.textContent = "Not saved"; });
  flush();
}

const ARROWS = { ArrowLeft: "left", ArrowRight: "right" };
addEventListener("keydown", (e) => {
  const repeat = e.repeat || pressed.has(e.code);
  pressed.add(e.code);
  log({ type: "keydown", key: e.key, repeat, marked: e.repeat, shift: e.shiftKey, ctrl: e.ctrlKey, alt: e.altKey },
      e.timeStamp);
  if (e.ctrlKey || e.altKey || e.metaKey) return;
  const arrow = ARROWS[e.key];
  if (arrow || e.key === "Shift" || e.key.toLowerCase() === "s") e.preventDefault();
  if (repeat) return;
  if (arrow || e.key === "Shift") {
    advance(e.timeStamp);
    if (arrow && !down[arrow]) {
      down[arrow] = true;
      turn = turn || { key: arrow, at: e.timeStamp, from: heading, fast: down.shift };
    } else if (e.key === "Shift") {
      down.shift = true;
      if (turn) turn.fast = true;
    }
    requestAnimationFrame(frame);
  } else if (e.key.toLowerCase() === "s" && !hold) {
    hold = { at: e.timeStamp, done: false };
    hold.timer = setTimeout(() => { if (hold && !hold.done) save(CONFIG.saveMs); }, CONFIG.saveMs);
    requestAnimationFrame(frame);
  }
}, { capture: true });

function release(key, t) {
  advance(t);
  if (key === "shift") down.shift = false;
  else down[key] = false;
  if (turn && !down.left && !down.right) {
    log({ type: "turn", key: turn.key, fast: turn.fast, ms: round1(t - turn.at), from: round3(turn.from),
          to: round3(heading) }, t);
    turn = null;
    post("/api/view", { heading: round3(heading) }).catch(() => {});
    flush();
  }
  draw();
}

addEventListener("keyup", (e) => {
  pressed.delete(e.code);
  log({ type: "keyup", key: e.key, shift: e.shiftKey }, e.timeStamp);
  const arrow = ARROWS[e.key];
  if (arrow && down[arrow]) release(arrow, e.timeStamp);
  else if (e.key === "Shift" && down.shift) release("shift", e.timeStamp);
  else if (e.key.toLowerCase() === "s" && hold) {
    clearTimeout(hold.timer);
    const held = e.timeStamp - hold.at;
    if (!hold.done && held >= CONFIG.saveMs) save(held);
    else if (!hold.done) {
      log({ type: "save_released", held_ms: round1(held) }, e.timeStamp);
      status.textContent = "Not saved: hold S for " + CONFIG.saveMs / 1000 + " seconds";
    }
    hold = null;
    fill.style.width = "0";
    flush();
  }
}, { capture: true });

// A window that loses the keyboard gets no release: the keys count as let go.
addEventListener("blur", () => {
  const t = performance.now();
  log({ type: "blur" }, t);
  pressed.clear();
  for (const key of ["left", "right", "shift"]) if (down[key]) release(key, t);
  if (hold) { clearTimeout(hold.timer); hold = null; fill.style.width = "0"; }
});
addEventListener("focus", () => log({ type: "focus" }));

addEventListener("resize", layout);
fetch("/api/state").then((r) => r.json()).then((state) => {
  heading = state.view;
  saved = state.saved;
  view.style.backgroundImage = "url(" + CONFIG.photo + ")";
  showSaved();
  const photo = new Image();
  photo.onload = layout;
  photo.src = CONFIG.photo;
});
"""


def page_html():
    config = json.dumps({"width": WIDTH, "height": HEIGHT, "fov": FOV, "speed": SPEED, "fast": FAST, "saveMs": SAVE_MS,
                         "start": VIEW["start"], "photo": PHOTO_PATH})
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(VIEW["title"])}</title>
<style>{STYLE}</style></head>
<body>
<header><span><span class="crumbs">{html.escape(VIEW["crumbs"])}</span>
<h1 style="display:inline">{html.escape(VIEW["heading"])}</h1></span>
<span class="status" id="status">All changes saved</span></header>
<main>
<div id="viewer"><canvas id="scale"></canvas>
<div id="view" role="img" aria-label="{html.escape(VIEW["alt"])}"><div id="centre"></div></div></div>
<aside id="panel"><h2>Opening view</h2>
<p>Readers see the panorama first as it is shown here. The line marks its centre.</p>
<p>Hold <kbd>←</kbd> or <kbd>→</kbd> to turn the view: {SPEED}° a second, or {FAST}° with <kbd>Shift</kbd> held
as well. The view turns for as long as the key is down.</p>
<p>Hold <kbd>S</kbd> for {SAVE_MS // 1000} seconds to save this view as the opening view.</p>
<div id="meter"><div id="fill"></div></div>
<p id="saved">No opening view saved yet.</p></aside>
</main>
<script>const CONFIG = {config};
{SCRIPT}</script>
</body></html>
"""


def load_state():
    path = os.path.join(STATE_DIR, "state.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"view": VIEW["start"], "saved": None}


def save_state(state):
    tmp = os.path.join(STATE_DIR, "state.json.tmp")
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, os.path.join(STATE_DIR, "state.json"))


def valid_heading(v):
    """Whether v is a direction the page could have sent: a number from 0 up to 360."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 360


def record(kind, heading, now=None):
    """Makes heading the view, and for a save also the saved opening view, and logs it. Returns the new state."""
    now = time.time() if now is None else now
    state = load_state()
    state["view"] = heading
    if kind == "save":
        state["saved"] = heading
    save_state(state)
    with open(os.path.join(STATE_DIR, "saves.jsonl" if kind == "save" else "views.jsonl"), "a") as f:
        f.write(json.dumps({"time": now, "heading": heading}) + "\n")
    return state


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
            with open(os.path.join(TARGET, VIEW["image"]), "rb") as f:
                self.send(200, f.read(), "image/jpeg")
        elif path == "/api/state":
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
        elif path in ("/api/view", "/api/save") and isinstance(body, dict):
            if not valid_heading(body.get("heading")):
                self.send(400, json.dumps({"error": "no such direction"}))
                return
            with LOCK:
                self.send(200, json.dumps(record(path.rsplit("/", 1)[1], body["heading"])))
        else:
            self.send(404, json.dumps({"error": "not found"}))

    def log_message(self, fmt, *args):
        pass


def load(target):
    """Makes the variant in the directory target the one served: its photo and view.json."""
    global TARGET, VIEW, WIDTH, HEIGHT, PAGE_PATH, PHOTO_PATH
    TARGET = target
    with open(os.path.join(target, "view.json")) as f:
        VIEW = json.load(f)
    WIDTH, HEIGHT = VIEW["size"]
    PAGE_PATH = VIEW["path"]
    PHOTO_PATH = PAGE_PATH.rsplit("/", 1)[0] + "/" + VIEW["image"]


if __name__ == "__main__":
    STATE_DIR = sys.argv[1]
    load(sys.argv[2])
    os.makedirs(STATE_DIR, exist_ok=True)
    save_state(load_state())
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PORT", "80"))), Handler).serve_forever()
