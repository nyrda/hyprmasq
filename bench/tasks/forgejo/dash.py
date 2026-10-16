"""The release dashboard: a web app that signs in to Forgejo with OAuth in a popup window. dash.py STATE-DIR

It listens on dash.example.test:8080. Its page has a "Connect Forgejo" button that opens /connect in a popup;
that redirects to Forgejo's authorization page, and Forgejo sends the popup back to /callback, which trades the
code for a token, looks the user up, tells the page through postMessage and closes the popup. The OAuth client,
which setup.py registers in Forgejo, is STATE-DIR/client.json; the connection goes to STATE-DIR/state.json,
which check.py reads.
"""

import html
import json
import os
import secrets
import sys
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FORGEJO = "http://git.example.test:3000"
SELF = "http://dash.example.test:8080"
STATE_DIR = sys.argv[1]
states = set()


def read(name):
    try:
        with open(os.path.join(STATE_DIR, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def forgejo(path, token=None, data=None):
    request = urllib.request.Request(FORGEJO + path, data=data)
    request.add_header("Accept", "application/json")
    if token:
        request.add_header("Authorization", f"token {token}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Release dashboard</title>
<style>
body {{ font-family: sans-serif; margin: 3em auto; max-width: 40em; color: #222; }}
button {{ font-size: 1.1em; padding: .5em 1.2em; }}
.box {{ border: 1px solid #ccc; border-radius: 6px; padding: 1em 1.5em; margin-top: 1.5em; }}
</style></head>
<body>
<h1>Release dashboard</h1>
<p>Track the releases of your Forgejo repositories.</p>
<div class="box">{body}</div>
<script>
function connect() {{
  window.open("/connect", "forgejo-sign-in", "popup,width=560,height=680");
}}
window.addEventListener("message", (event) => {{
  if (event.origin === location.origin && event.data === "connected") location.reload();
}});
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type="text/html; charset=utf-8", headers=()):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        if url.path == "/":
            state = read("state.json")
            if state:
                repos = "".join(f"<li>{html.escape(r)}</li>" for r in state["repositories"])
                body = (f"<p>Connected to Forgejo as <b>{html.escape(state['user'])}</b>.</p>"
                        f"<p>Repositories:</p><ul>{repos}</ul>")
            else:
                body = ('<p>Not connected.</p>'
                        '<button onclick="connect()">Connect Forgejo</button>')
            self.send(200, PAGE.format(body=body))
        elif url.path == "/connect":
            client = read("client.json")
            if not client:
                self.send(503, "The dashboard is not set up yet.", "text/plain")
                return
            state = secrets.token_urlsafe(16)
            states.add(state)
            target = FORGEJO + "/login/oauth/authorize?" + urllib.parse.urlencode({
                "client_id": client["client_id"],
                "redirect_uri": SELF + "/callback",
                "response_type": "code",
                "state": state,
            })
            self.send(302, "", headers=[("Location", target)])
        elif url.path == "/callback":
            client = read("client.json")
            state = query.get("state", [""])[0]
            code = query.get("code", [""])[0]
            if not client or state not in states or not code:
                self.send(400, "<p>The sign-in failed. Close this window and try again.</p>")
                return
            states.discard(state)
            token = forgejo("/login/oauth/access_token", data=urllib.parse.urlencode({
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": SELF + "/callback",
            }).encode())["access_token"]
            user = forgejo("/api/v1/user", token)["login"]
            repos = [r["full_name"] for r in forgejo("/api/v1/user/repos", token)]
            with open(os.path.join(STATE_DIR, "state.json"), "w") as f:
                json.dump({"user": user, "repositories": repos, "time": time.time()}, f)
            self.send(200, "<p>Connected. You can close this window.</p>"
                           "<script>if (window.opener) window.opener.postMessage('connected', '*');"
                           "window.close();</script>")
        else:
            self.send(404, "Not found", "text/plain")

    def log_message(self, fmt, *args):
        print("%s %s" % (time.time(), fmt % args), flush=True)


if __name__ == "__main__":
    os.makedirs(STATE_DIR, exist_ok=True)
    ThreadingHTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
