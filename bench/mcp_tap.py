#!/usr/bin/env python3
"""A stdio MCP server that forwards to hyprmasq-mcp in the benchmark VM and logs every message.

    mcp_tap.py CID PORT LOG [MAX_CALLS]

The agent starts it as its MCP server. It connects to the VM over vsock, where socat starts one hyprmasq-mcp
per connection, and passes newline-delimited JSON-RPC both ways unchanged. Each message is logged to LOG as a
JSON line with the host time it passed: `dir` is `request` (agent to server) or `response`, and images are
replaced by their size, so the log stays small and tool time is measured outside both the agent and the VM.

With MAX_CALLS above 0, the tool calls after the first MAX_CALLS do not reach the server. The tap answers each with
an error, logs it with `dir` `budget`, and creates LOG.budget, from which run.py stops the agent.
"""

import hashlib
import json
import socket
import sys
import threading
import time


def summarize(message):
    """The message without image data, but its size and its hash, which say whether two screenshots are the same."""

    def strip(value):
        if isinstance(value, dict):
            if value.get("type") == "image" and isinstance(value.get("data"), str):
                return {"type": "image", "mimeType": value.get("mimeType"), "bytes": len(value["data"]) * 3 // 4,
                        "sha256": hashlib.sha256(value["data"].encode()).hexdigest()}
            return {k: strip(v) for k, v in value.items()}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    return strip(message)


def budget_reply(message, calls, max_calls):
    """The tap's own answer to a tools/call request past the budget, or None to forward the message."""
    if max_calls <= 0 or message.get("method") != "tools/call" or calls <= max_calls:
        return None
    text = f"The benchmark's budget of {max_calls} tool calls is spent. The run ends here."
    return {"jsonrpc": "2.0", "id": message.get("id"),
            "result": {"content": [{"type": "text", "text": text}], "isError": True}}


def main(cid, port, log_path, max_calls="0"):
    max_calls = int(max_calls)
    sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    sock.connect((int(cid), int(port)))
    log = open(log_path, "a", buffering=1)
    lock = threading.Lock()
    out = threading.Lock()
    calls = 0

    def send(line):
        with out:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

    def record(direction, line):
        now = time.time()
        try:
            entry = {"t": now, "dir": direction, "msg": summarize(json.loads(line))}
        except ValueError:
            entry = {"t": now, "dir": direction, "raw": line[:500].decode(errors="replace")}
        with lock:
            log.write(json.dumps(entry) + "\n")

    def upstream():
        nonlocal calls
        for line in sys.stdin.buffer:
            try:
                message = json.loads(line)
            except ValueError:
                message = {}
            if isinstance(message, dict) and message.get("method") == "tools/call":
                calls += 1
            reply = budget_reply(message, calls, max_calls) if isinstance(message, dict) else None
            if reply:
                with lock:
                    log.write(json.dumps({"t": time.time(), "dir": "budget", "calls": calls - 1,
                                          "msg": summarize(message)}) + "\n")
                open(log_path + ".budget", "a").close()
                send((json.dumps(reply) + "\n").encode())
                continue
            record("request", line)
            sock.sendall(line)
        sock.shutdown(socket.SHUT_WR)

    threading.Thread(target=upstream, daemon=True).start()
    reader = sock.makefile("rb")
    for line in reader:
        record("response", line)
        send(line)


if __name__ == "__main__":
    if sys.argv[1:] == ["--help"]:
        print(__doc__)
    else:
        main(*sys.argv[1:5])
