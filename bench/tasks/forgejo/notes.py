"""The page the user types into while the agent works. notes.py STATE-DIR

It listens on notes.example.test:8081 and serves a text area that has the focus. Each change sends the whole
text with a sequence number to /save, which writes the newest to STATE-DIR/notes.json with the time, so that check.py can compare it with what
the user typed: keys that went elsewhere while the agent worked are missing there.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE_DIR = sys.argv[1]
LOCK = threading.Lock()
LATEST = 0

PAGE = b"""<!doctype html>
<html><head><meta charset="utf-8"><title>Meeting notes</title>
<style>body { font-family: sans-serif; margin: 2em; } textarea { width: 100%; height: 70vh; font-size: 1.1em; }</style>
</head><body>
<h1>Meeting notes</h1>
<textarea id="notes" autofocus></textarea>
<script>
const notes = document.getElementById("notes");
let seq = 0;
notes.addEventListener("input", () => {
  seq += 1;
  fetch("/save", { method: "POST", body: JSON.stringify({ seq: seq, text: notes.value }) });
});
window.addEventListener("focus", () => notes.focus());
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", "replace"))
        # The page saves after every key, and requests can finish out of order: keep only the newest text.
        with LOCK:
            global LATEST
            if body["seq"] > LATEST:
                LATEST = body["seq"]
                with open(os.path.join(STATE_DIR, "notes.json.tmp"), "w") as f:
                    json.dump({"text": body["text"], "seq": body["seq"], "time": time.time()}, f)
                os.replace(os.path.join(STATE_DIR, "notes.json.tmp"), os.path.join(STATE_DIR, "notes.json"))
        self.send_response(204)
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    os.makedirs(STATE_DIR, exist_ok=True)
    ThreadingHTTPServer(("127.0.0.1", 8081), Handler).serve_forever()
