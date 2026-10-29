#!/usr/bin/env python3
"""Calls hyprmasq-mcp in the benchmark VM through mcp_tap.py, for checking a VM by hand.

    mcp_probe.py CID TOOL [JSON-ARGS] [TOOL [JSON-ARGS]]... [--image OUT.png] [--log LOG]

Makes the calls in one session, in order, so that a call can use the shots of the ones before: "@shot" in the
arguments stands for the shot of the latest result that had one. Prints the text of each result and saves the first
image of the last one to OUT.png. The tap logs the session to LOG, by default /tmp/hyprmasq-bench-probe.jsonl.
"""

import base64
import json
import os
import subprocess
import sys


class Session:
    """An MCP session with hyprmasq-mcp in the VM on vsock CID cid, through mcp_tap.py, which logs it to log."""

    def __init__(self, cid, log, client="hyprmasq-bench-probe"):
        tap = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_tap.py")
        self.proc = subprocess.Popen([sys.executable, tap, str(cid), "5000", log], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE)
        self.id = 1
        self.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                    "clientInfo": {"name": client, "version": "0"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, message):
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        self.proc.stdin.flush()

    def request(self, method, params):
        self.id += 1
        self.send({"jsonrpc": "2.0", "id": self.id, "method": method, "params": params})
        for line in self.proc.stdout:
            message = json.loads(line)
            if message.get("id") == self.id:
                return message
        raise EOFError("the MCP server closed the connection")

    def call(self, tool, arguments):
        """The result of a tool call."""
        return self.request("tools/call", {"name": tool, "arguments": arguments})["result"]

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


def main():
    args = sys.argv[1:]
    options = {"--image": None, "--log": "/tmp/hyprmasq-bench-probe.jsonl"}
    for option in options:
        if option in args:
            i = args.index(option)
            options[option] = args[i + 1]
            del args[i : i + 2]
    image_out = options["--image"]
    cid, rest = args[0], args[1:]
    calls = []
    while rest:
        tool, rest = rest[0], rest[1:]
        arguments = {}
        if rest and rest[0].lstrip().startswith("{"):
            arguments, rest = rest[0], rest[1:]
        calls.append((tool, arguments))
    session = Session(cid, options["--log"])
    shot = None
    for n, (tool, arguments) in enumerate(calls, 1):
        if arguments:
            arguments = json.loads(arguments.replace('"@shot"', json.dumps(shot)))
        result = session.call(tool, arguments)
        last = n == len(calls)
        for item in result.get("content", []):
            if item["type"] == "text":
                print(item["text"])
            elif item["type"] == "image" and image_out and last:
                with open(image_out, "wb") as f:
                    f.write(base64.b64decode(item["data"]))
                image_out = None
        structured = result.get("structuredContent") or {}
        # windows gives the shot of the window it matched in its entry.
        for entry in [structured] + (structured.get("windows") or []):
            shot = (entry.get("shot") or {}).get("id") or shot
        if structured:
            print(json.dumps(structured, indent=1)[:3000])
    session.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["--help"]:
        print(__doc__)
    else:
        main()
