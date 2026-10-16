#!/usr/bin/env python3
"""Calls hyprmasq-mcp in the benchmark VM through mcp_tap.py, for checking a VM by hand.

    mcp_probe.py CID TOOL [JSON-ARGS] [--image OUT.png]

Prints the text of the result and saves its first image to OUT.png.
"""

import base64
import json
import os
import subprocess
import sys


def main():
    args = sys.argv[1:]
    image_out = None
    if "--image" in args:
        i = args.index("--image")
        image_out = args[i + 1]
        del args[i : i + 2]
    cid, tool = args[0], args[1]
    arguments = json.loads(args[2]) if len(args) > 2 else {}
    tap = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_tap.py")
    proc = subprocess.Popen(
        [sys.executable, tap, cid, "5000", "/tmp/hyprmasq-bench-probe.jsonl"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )

    def send(message):
        proc.stdin.write((json.dumps(message) + "\n").encode())
        proc.stdin.flush()

    def receive(want_id):
        for line in proc.stdout:
            message = json.loads(line)
            if message.get("id") == want_id:
                return message

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "hyprmasq-bench-probe", "version": "0"}}})
    receive(1)
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}})
    result = receive(2)["result"]
    for item in result.get("content", []):
        if item["type"] == "text":
            print(item["text"])
        elif item["type"] == "image" and image_out:
            with open(image_out, "wb") as f:
                f.write(base64.b64decode(item["data"]))
            image_out = None
    if result.get("structuredContent"):
        print(json.dumps(result["structuredContent"], indent=1)[:3000])
    proc.stdin.close()
    proc.wait(timeout=10)


if __name__ == "__main__":
    main()
