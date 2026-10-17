"""Mara's build guide, where she labels the figures: guide.py STATE-DIR VARIANT

It listens on guide.example.test:80 and serves one figure, the photo of her motherboard, with a tray of eight
labels to drag onto it. VARIANT is how the page drags:

- dnd: HTML5 drag-and-drop (draggable, dragstart, dragover, drop), which Firefox does with Wayland's DnD;
- pointer: pointerdown, pointermove and pointerup with pointer capture, as a custom widget would.

A label lands where it is dropped: its point, in photo pixels, is the pointer's position at the drop. The page saves
every drop, and STATE-DIR/board.json holds where each label is (null in the tray), which the grade reads; every save
goes to STATE-DIR/places.jsonl with the time. The page also logs every pointer and drag event it gets, its layout,
and each drop it applies, to STATE-DIR/events.jsonl, for attribute.py.
"""

import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REGIONS = json.load(open(os.path.join(HERE, "regions.json")))
WIDTH, HEIGHT = REGIONS["size"]
# The tray's order, the same on every load.
LABELS = ["First PCIe slot", "Memory slots", "BMC chip", "24-pin ATX power", "SATA ports", "CPU socket",
          "USB 3.0 header", "8-pin CPU power"]
assert sorted(LABELS) == sorted(REGIONS["labels"])
PAGE_PATH = "/figures/motherboard"
PHOTO_PATH = "/figures/motherboard.jpg"
VARIANTS = ("dnd", "pointer")

STATE_DIR = None  # set by main
VARIANT = None
LOCK = threading.Lock()

STYLE = """
html, body { margin: 0; height: 100%; overflow: hidden; }
body { font-family: "DejaVu Sans", sans-serif; font-size: 15px; color: #1f2933; background: #eef1f4;
       user-select: none; -moz-user-select: none; }
header { height: 44px; box-sizing: border-box; display: flex; align-items: center; justify-content: space-between;
         padding: 0 16px; background: #2d3e50; color: #fff; }
header .crumbs { opacity: 0.8; }
header h1 { font-size: 17px; margin: 0; font-weight: bold; }
header .status { font-size: 14px; opacity: 0.9; }
main { display: flex; gap: 16px; padding: 16px; align-items: flex-start; }
#figure { position: relative; flex: none; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3); background: #fff; }
#photo { display: block; width: 100%; height: 100%; }
#tray { width: 220px; flex: none; box-sizing: border-box; background: #fff; border: 1px solid #c8d1db;
        border-radius: 6px; padding: 10px 12px; }
#tray h2 { font-size: 16px; margin: 0 0 4px; }
#tray p { font-size: 13px; color: #52606d; margin: 0 0 10px; line-height: 1.35; }
.slot { height: 34px; margin-bottom: 8px; border: 1px dashed #c8d1db; border-radius: 4px; box-sizing: border-box; }
.chip, .tag { display: inline-block; background: #fff; border: 1px solid #1f2933; border-radius: 4px;
              white-space: nowrap; cursor: grab; touch-action: none; }
.chip { font-size: 15px; height: 32px; line-height: 30px; padding: 0 10px; box-sizing: border-box; }
.slot .chip { display: block; }
.pin { position: absolute; width: 0; height: 0; touch-action: none; }
.dot { position: absolute; left: -7px; top: -7px; width: 10px; height: 10px; border-radius: 50%;
       background: #ffd400; border: 2px solid #111; cursor: grab; }
.tag { position: absolute; top: -11px; left: 10px; font-size: 13px; line-height: 18px; padding: 1px 6px;
       box-shadow: 0 1px 2px rgba(0, 0, 0, 0.4); }
.pin.left .tag { left: auto; right: 10px; }
#ghost { position: fixed; left: -1000px; top: 0; width: 220px; height: 24px; pointer-events: none; z-index: 10; }
#ghost .dot { left: 0; top: 5px; }
#ghost .tag { left: 17px; top: 2px; }
#ghost.hidden { display: none; }
"""

SCRIPT = """
const PHOTO = { width: CONFIG.width, height: CONFIG.height };
const LABELS = CONFIG.labels;
const figure = document.getElementById("figure");
const photo = document.getElementById("photo");
const tray = document.getElementById("tray");
const status = document.getElementById("status");
const ghost = document.getElementById("ghost");
let state = Object.fromEntries(LABELS.map((l) => [l, null]));

// The page's log of pointer and drag events, its layout and its drops, sent to the server in batches.
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
  if (inside(tray.getBoundingClientRect(), x, y)) return "tray";
  return "page";
}
const rect = (r) => [r.left, r.top, r.right, r.bottom].map((v) => Math.round(v * 10) / 10);
const labelOf = (target) => {
  const el = target && target.closest ? target.closest("[data-label]") : null;
  return el ? el.dataset.label : null;
};

for (const type of ["pointerdown", "pointermove", "pointerup", "pointercancel", "gotpointercapture",
                    "lostpointercapture", "dragstart", "drag", "dragenter", "dragover", "dragleave", "drop", "dragend"]) {
  addEventListener(type, (e) => {
    const entry = { type, x: e.clientX, y: e.clientY, sx: e.screenX, sy: e.screenY, buttons: e.buttons,
                    label: labelOf(e.target), over: where(e.clientX, e.clientY) };
    if (e.dataTransfer) entry.effect = e.dataTransfer.dropEffect;
    log(entry);
  }, { capture: true, passive: true });
}

function layout() {
  const header = document.querySelector("header").offsetHeight;
  const availW = innerWidth - tray.offsetWidth - 3 * 16;
  const availH = innerHeight - header - 2 * 16;
  const scale = Math.min(1, availW / PHOTO.width, availH / PHOTO.height);
  figure.style.width = Math.floor(PHOTO.width * scale) + "px";
  figure.style.height = Math.floor(PHOTO.height * scale) + "px";
  render();
}

function chip(label, cls) {
  const el = document.createElement("span");
  el.className = cls;
  el.dataset.label = label;
  el.textContent = label;
  if (CONFIG.variant === "dnd") el.draggable = true;
  return el;
}

function render() {
  const scale = figure.clientWidth / PHOTO.width;
  for (const old of figure.querySelectorAll(".pin")) old.remove();
  const slots = tray.querySelectorAll(".slot");
  LABELS.forEach((label, i) => {
    slots[i].replaceChildren();
    const point = state[label];
    if (!point) {
      slots[i].append(chip(label, "chip"));
      return;
    }
    const pin = document.createElement("div");
    pin.className = "pin" + (point[0] * scale > figure.clientWidth - 190 ? " left" : "");
    pin.dataset.label = label;
    pin.style.left = point[0] * scale + "px";
    pin.style.top = point[1] * scale + "px";
    const dot = document.createElement("span");
    dot.className = "dot";
    if (CONFIG.variant === "dnd") dot.draggable = true;
    pin.append(dot, chip(label, "tag"));
    figure.append(pin);
  });
  const labels = {};
  for (const el of [...figure.querySelectorAll(".tag, .dot"), ...tray.querySelectorAll(".chip")]) {
    const label = el.dataset.label || el.parentNode.dataset.label;
    (labels[label] = labels[label] || []).push(rect(el.getBoundingClientRect()));
  }
  log({ type: "layout", variant: CONFIG.variant, inner: [innerWidth, innerHeight], dpr: devicePixelRatio,
        screen: [window.mozInnerScreenX, window.mozInnerScreenY], scale, photo: rect(photo.getBoundingClientRect()),
        tray: rect(tray.getBoundingClientRect()), labels, state });
}

// Saves are sent one after another, in the order of the drops.
let saving = Promise.resolve();
function save(label, point) {
  status.textContent = "Saving…";
  saving = saving.then(() => fetch("/api/labels", { method: "POST", headers: { "Content-Type": "application/json" },
                                                     body: JSON.stringify({ label, point }) }))
    .then((r) => { status.textContent = r.ok ? "All changes saved" : "Not saved"; })
    .catch(() => { status.textContent = "Not saved"; });
}

// A label dropped on the photo goes to the drop point, one dropped on the tray back to the tray; elsewhere, it stays.
function dropAt(label, x, y, via) {
  const r = photo.getBoundingClientRect();
  const to = where(x, y);
  if (to === "page") {
    log({ type: "place", label, x, y, via, to });
    flush();
    return;
  }
  const scale = r.width / PHOTO.width;
  const clamp = (v, max) => Math.min(max, Math.max(0, Math.round(v * 10) / 10));
  const point = to === "photo" ? [clamp((x - r.left) / scale, PHOTO.width), clamp((y - r.top) / scale, PHOTO.height)] : null;
  log({ type: "place", label, x, y, via, to, point, before: state[label] });
  state = { ...state, [label]: point };
  render();
  save(label, point);
  flush();
}

// What follows the pointer: a label's dot, centred on HOT, and its tag.
const HOT = [7, 12];
function showGhost(label, x, y) {
  const tag = Object.assign(document.createElement("span"), { className: "tag", textContent: label });
  ghost.replaceChildren(Object.assign(document.createElement("span"), { className: "dot" }), tag);
  ghost.classList.remove("hidden");
  ghost.style.left = x - HOT[0] + "px";
  ghost.style.top = y - HOT[1] + "px";
}

if (CONFIG.variant === "dnd") {
  // A drop is applied at dragend, which goes to the dragged element, before the drop renders it away.
  let dragging = null, dropped = null;
  const apply = () => {
    if (!dropped) return;
    const { label, x, y } = dropped;
    dropped = null;
    dropAt(label, x, y, "drop");
  };
  addEventListener("dragstart", (e) => {
    dragging = labelOf(e.target);
    if (!dragging) return;
    e.dataTransfer.setData("text/plain", dragging);
    e.dataTransfer.effectAllowed = "move";
    showGhost(dragging, -1000, 40);
    e.dataTransfer.setDragImage(ghost, HOT[0], HOT[1]);
  });
  addEventListener("dragover", (e) => {
    if (!dragging) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  });
  addEventListener("drop", (e) => {
    if (!dragging) return;
    e.preventDefault();
    const label = e.dataTransfer.getData("text/plain") || dragging;
    if (!LABELS.includes(label)) return;
    dropped = { label, x: e.clientX, y: e.clientY };
    setTimeout(apply, 500);
  });
  addEventListener("dragend", () => { dragging = null; ghost.classList.add("hidden"); apply(); });
} else {
  // A press that moves less than this is no drag, so that a click on a placed label leaves it where it is.
  const SLOP = 4;
  let drag = null;
  addEventListener("dragstart", (e) => e.preventDefault());
  addEventListener("pointerdown", (e) => {
    const label = labelOf(e.target);
    if (e.button !== 0 || !label || drag) return;
    e.preventDefault();
    e.target.setPointerCapture(e.pointerId);
    drag = { label, id: e.pointerId, x: e.clientX, y: e.clientY, moved: false };
  });
  addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    drag.moved = drag.moved || Math.hypot(e.clientX - drag.x, e.clientY - drag.y) >= SLOP;
    if (drag.moved) showGhost(drag.label, e.clientX, e.clientY);
  });
  addEventListener("pointerup", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const { label, moved } = drag;
    drag = null;
    ghost.classList.add("hidden");
    if (moved) dropAt(label, e.clientX, e.clientY, "pointerup");
  });
  for (const type of ["pointercancel", "lostpointercapture"]) {
    addEventListener(type, (e) => {
      if (drag && e.pointerId === drag.id) { drag = null; ghost.classList.add("hidden"); }
    });
  }
}

addEventListener("resize", layout);
fetch("/api/labels").then((r) => r.json()).then((saved) => {
  state = { ...state, ...saved.labels };
  if (photo.complete) layout(); else photo.addEventListener("load", layout);
});
"""


def page_html():
    config = json.dumps({"variant": VARIANT, "labels": LABELS, "width": WIDTH, "height": HEIGHT})
    slots = '<div class="slot"></div>' * len(LABELS)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Figure 3: The motherboard · Build guide</title>
<style>{STYLE}</style></head>
<body>
<header><span><span class="crumbs">Build guide › Figures ›</span> <h1 style="display:inline">Figure 3: The motherboard</h1></span>
<span class="status" id="status">All changes saved</span></header>
<main>
<div id="figure" style="width:{WIDTH // 2}px;height:{HEIGHT // 2}px"><img id="photo" src="{PHOTO_PATH}" alt="The motherboard"
 draggable="false"></div>
<aside id="tray"><h2>Labels</h2><p>Drag each label onto its part of the photo. Drag a placed label to move it, or
back here to take it off.</p>{slots}</aside>
</main>
<div id="ghost" class="hidden"></div>
<script>const CONFIG = {config};
{SCRIPT}</script>
</body></html>
"""


def load_state():
    path = os.path.join(STATE_DIR, "board.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"variant": VARIANT, "labels": {label: None for label in LABELS}}


def save_state(state):
    tmp = os.path.join(STATE_DIR, "board.json.tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, os.path.join(STATE_DIR, "board.json"))


def valid_point(point):
    return point is None or (
        isinstance(point, list) and len(point) == 2
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in point)
        and 0 <= point[0] <= WIDTH and 0 <= point[1] <= HEIGHT
    )


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
            with open(os.path.join(HERE, "motherboard.jpg"), "rb") as f:
                self.send(200, f.read(), "image/jpeg")
        elif path == "/api/labels":
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
        elif path == "/api/labels" and isinstance(body, dict):
            label, point = body.get("label"), body.get("point")
            if label not in LABELS or not valid_point(point):
                self.send(400, json.dumps({"error": "no such label or point"}))
                return
            with LOCK:
                state = load_state()
                before = state["labels"][label]
                state["labels"][label] = point
                save_state(state)
                with open(os.path.join(STATE_DIR, "places.jsonl"), "a") as f:
                    f.write(json.dumps({"time": time.time(), "label": label, "point": point, "before": before}) + "\n")
                self.send(200, json.dumps(state))
        else:
            self.send(404, json.dumps({"error": "not found"}))

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    STATE_DIR, VARIANT = sys.argv[1], sys.argv[2]
    if VARIANT not in VARIANTS:
        sys.exit(f"variant must be one of {', '.join(VARIANTS)}, not {VARIANT}")
    os.makedirs(STATE_DIR, exist_ok=True)
    save_state(load_state())
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PORT", "80"))), Handler).serve_forever()
