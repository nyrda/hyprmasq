"""Root's log of Hyprland's events for the form task: windows.py SOCKET2 LOG.

It writes each event of Hyprland's event socket, SOCKET2, to LOG with the time, as the benchmark's own logger does,
and after each openwindow a line windowpid>>ADDRESS,PID with the process of that window, which it asks Hyprland's
request socket beside SOCKET2 for. check.py tells by it the windows of the questionnaire's own process, such as the
popup of a dropdown that an X11 app opens as a window, from the others.
"""

import json
import os
import socket
import sys
import time


def window_pid(requests, address):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.connect(requests)
        sock.sendall(b"j/clients")
        reply = b""
        while chunk := sock.recv(65536):
            reply += chunk
    for client in json.loads(reply):
        if client["address"] == address:
            return client["pid"]
    return None


def main():
    events, path = sys.argv[1], sys.argv[2]
    requests = os.path.join(os.path.dirname(events), ".socket.sock")
    sock = socket.socket(socket.AF_UNIX)
    sock.connect(events)
    out = open(path, "a", buffering=1)
    for line in sock.makefile("r", errors="replace"):
        out.write(f"{time.time():.3f} {line}")
        name, _, data = line.rstrip("\n").partition(">>")
        if name == "openwindow":
            address = "0x" + data.split(",", 1)[0].removeprefix("0x")
            try:
                pid = window_pid(requests, address)
            except (OSError, ValueError) as error:
                print(f"windows.py: no process of {address}: {error}", file=sys.stderr)
                pid = None
            if pid is not None:
                out.write(f"{time.time():.3f} windowpid>>{address},{pid}\n")


if __name__ == "__main__":
    main()
