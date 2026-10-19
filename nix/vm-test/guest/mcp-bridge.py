import base64, json, os, socket, subprocess, sys, threading, time

name = sys.argv[1]
path = f"/tmp/hm-m-{name}.sock"
os.makedirs("/tmp/hm-m-images", exist_ok=True)
server = subprocess.Popen(["hyprmasq-mcp", *sys.argv[2:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=open(f"/tmp/hyprmasq-m-{name}-server.log", "wb"))
changed = threading.Condition()
responses, notifications, sent = {}, [], {}
session = {"last": 0, "closed": False}


def read():
    for line in server.stdout:
        message = json.loads(line)
        message["received"] = time.monotonic()
        with changed:
            if "method" in message:
                notifications.append(message)
            else:
                for i, item in enumerate((message.get("result") or {}).get("content") or []):
                    if item.get("type") == "image":
                        item["path"] = f"/tmp/hm-m-images/{name}-{message['id']}-{i}.png"
                        with open(item["path"], "wb") as image:
                            image.write(base64.b64decode(item.pop("data")))
                message["sent"] = sent.get(message.get("id"))
                responses[message.get("id")] = message
            changed.notify_all()
    with changed:
        session["closed"] = True
        changed.notify_all()


def write(message):
    server.stdin.write(json.dumps(message).encode() + b"\n")
    server.stdin.flush()


def response(id, timeout):
    with changed:
        changed.wait_for(lambda: id in responses or session["closed"], timeout)
        return responses.get(id, {"missing": id, "closed": session["closed"]})


def handle(command):
    op, now = command["op"], time.monotonic()
    if op == "request":
        with changed:
            session["last"] += 1
            id = session["last"]
            sent[id] = now
            write({"jsonrpc": "2.0", "id": id, "method": command["method"], "params": command["params"]})
        return response(id, command["timeout"]) if command["wait"] else {"id": id, "sent": now}
    if op == "response":
        return response(command["id"], command["timeout"])
    if op == "notify":
        with changed:
            write({"jsonrpc": "2.0", "method": command["method"], "params": command["params"]})
        return {"sent": now}
    if op == "notifications":
        with changed:
            return {"notifications": notifications}
    if op == "pid":
        return {"pid": server.pid}
    if op == "stop":
        server.stdin.close()
        try:
            return {"status": server.wait(command["timeout"]), "stopped": time.monotonic()}
        except subprocess.TimeoutExpired:
            return {"status": None}
    raise ValueError(op)


def serve(connection):
    with connection, connection.makefile("rwb") as stream:
        command = json.loads(stream.readline())
        stream.write(json.dumps(handle(command)).encode() + b"\n")
        stream.flush()
    if command["op"] == "stop":
        os.unlink(path)
        os._exit(0)


threading.Thread(target=read, daemon=True).start()
listener = socket.socket(socket.AF_UNIX)
listener.bind(path)
listener.listen()
while True:
    threading.Thread(target=serve, args=(listener.accept()[0],), daemon=True).start()
