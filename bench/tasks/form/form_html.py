"""The form task's questionnaire as an HTML page in a browser engine: form_html.py --browser COMMAND STATE-DIR COUNT
[STATE-FD].

This process serves form.html and the first COUNT questions of items.py on localhost and starts COMMAND URL, the
browser engine that shows the page, as its child. The page sends it every change of an answer and every press of
Submit, and it keeps the answers, the submissions and the events in its memory and logs them as form.py does: every
change with the time to STATE-DIR/events.jsonl, with how a slider moved, named as form.py names GTK's scroll types
(jump for the pointer, step-forward and the like for keys), and every press of Submit to submissions.jsonl. On
SIGUSR1 it writes its state to the file descriptor STATE-FD, or without it to STATE-DIR/state.json, which check.py
grades as the other apps' state. The engine does not inherit STATE-FD. It quits when the engine does.

The server listens on 127.0.0.1 only, under a random path that it gives only to the engine, so that another process
that does not read the engine's command line cannot send it answers.
"""

import argparse
import http.server
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import items  # noqa: E402

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "form.html")


class Form:
    def __init__(self, state_dir, count, state_fd):
        self.state_dir = state_dir
        self.state_fd = state_fd
        self.count = count
        self.items = {item["number"]: item for item in items.items(count)}
        self.values = {number: items.initial(item) for number, item in self.items.items()}
        self.events = []
        self.submissions = []
        self.lock = threading.Lock()

    def questions(self):
        """The questions for the page, with what it shows of a slider, and the answers it holds."""
        shown = []
        for item in self.items.values():
            item = dict(item)
            if item["kind"] == "slider":
                step = item["step"]
                item["decimals"] = items.decimals(step)
                item["caption"] = items.range_text(item)
                item["ends"] = [items.show(item["lowest"], step), items.show(item["highest"], step)]
            shown.append(item)
        with self.lock:
            values = {str(n): v for n, v in self.values.items()}
        return {"count": self.count, "items": shown, "values": values}

    def change(self, number, value, how=None):
        item = self.items[number]
        valid = {"checkbox": lambda v: isinstance(v, list) and all(o in item["options"] for o in v),
                 "dropdown": lambda v: v in item["options"]}.get(item["kind"], lambda v: isinstance(v, str))
        if not valid(value):
            raise ValueError(f"not an answer to question {number}: {value!r}")
        with self.lock:
            if value == self.values[number]:
                return
            self.values[number] = value
            event = {"time": time.time(), "number": number, "kind": item["kind"], "value": value}
            if how:
                event["how"] = how
            self.events.append(event)
            self.append("events.jsonl", event)

    def submit(self):
        with self.lock:
            submission = {"time": time.time(), "values": {str(n): v for n, v in self.values.items()}}
            self.submissions.append(submission)
            self.append("submissions.jsonl", submission)
        return f"Submitted at {time.strftime('%H:%M:%S', time.localtime(submission['time']))}."

    def append(self, name, record):
        with open(os.path.join(self.state_dir, name), "a") as f:
            f.write(json.dumps(record) + "\n")

    def dump(self):
        with self.lock:
            state = {"count": self.count, "values": {str(n): v for n, v in self.values.items()},
                     "submissions": list(self.submissions), "events": list(self.events)}
        if self.state_fd is not None:
            data = json.dumps(state, indent=2).encode()
            os.ftruncate(self.state_fd, 0)
            os.pwrite(self.state_fd, data, 0)
            return
        path = os.path.join(self.state_dir, "state.json")
        with open(path + ".tmp", "w") as f:
            json.dump(state, f, indent=2)
        os.replace(path + ".tmp", path)


def handler(form, prefix):
    class Handler(http.server.BaseHTTPRequestHandler):
        def reply(self, status, body, content_type="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == prefix:
                with open(PAGE, "rb") as f:
                    self.reply(200, f.read(), "text/html; charset=utf-8")
            elif self.path == prefix + "questions":
                self.reply(200, form.questions())
            else:
                self.reply(404, {"error": "not found"})

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if self.path == prefix + "change":
                    form.change(int(body["number"]), body["value"], body.get("how"))
                    self.reply(200, {})
                elif self.path == prefix + "submit":
                    self.reply(200, {"status": form.submit()})
                else:
                    self.reply(404, {"error": "not found"})
            except (KeyError, TypeError, ValueError) as error:
                self.reply(400, {"error": str(error)})

        def log_message(self, format, *args):
            if self.path.startswith(prefix):
                return
            super().log_message(format, *args)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--browser", required=True, help="the engine's command, which gets the page's URL")
    parser.add_argument("state_dir")
    parser.add_argument("count", type=int)
    parser.add_argument("state_fd", type=int, nargs="?")
    args = parser.parse_args()
    items.items(args.count)
    os.makedirs(args.state_dir, exist_ok=True)
    form = Form(args.state_dir, args.count, args.state_fd)

    prefix = f"/{secrets.token_urlsafe(16)}/"
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler(form, prefix))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}{prefix}"

    # The handler runs in this thread, which only waits for the engine below and so holds no lock.
    signal.signal(signal.SIGUSR1, lambda *_: form.dump())
    engine = subprocess.Popen([args.browser, url])
    signal.signal(signal.SIGTERM, lambda *_: engine.terminate())
    sys.exit(engine.wait())


if __name__ == "__main__":
    main()
