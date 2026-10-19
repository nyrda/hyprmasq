#!/usr/bin/env python3
"""A stdio MCP server that forwards to hyprmasq-mcp in the benchmark VM and logs every message.

    mcp_tap.py CID PORT LOG

The agent starts it as its MCP server. It connects to the VM over vsock, where socat starts one hyprmasq-mcp
per connection, and passes newline-delimited JSON-RPC both ways unchanged. Each message is logged to LOG as a
JSON line with the host time it passed: `dir` is `request` (agent to server) or `response`, and images are
replaced by their size, so the log stays small and tool time is measured outside both the agent and the VM.
"""

import json
import socket
import sys
import threading
import time


def summarize(message):
    """The message without image data."""

    def strip(value):
        if isinstance(value, dict):
            if value.get("type") == "image" and isinstance(value.get("data"), str):
                return {"type": "image", "mimeType": value.get("mimeType"), "bytes": len(value["data"]) * 3 // 4}
            return {k: strip(v) for k, v in value.items()}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    return strip(message)


def main(cid, port, log_path):
    sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    sock.connect((int(cid), int(port)))
    log = open(log_path, "a", buffering=1)
    lock = threading.Lock()

    def record(direction, line):
        now = time.time()
        try:
            entry = {"t": now, "dir": direction, "msg": summarize(json.loads(line))}
        except ValueError:
            entry = {"t": now, "dir": direction, "raw": line[:500].decode(errors="replace")}
        with lock:
            log.write(json.dumps(entry) + "\n")

    def upstream():
        for line in sys.stdin.buffer:
            record("request", line)
            sock.sendall(line)
        sock.shutdown(socket.SHUT_WR)

    threading.Thread(target=upstream, daemon=True).start()
    reader = sock.makefile("rb")
    for line in reader:
        record("response", line)
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    if sys.argv[1:] == ["--help"]:
        print(__doc__)
    else:
        main(*sys.argv[1:4])
