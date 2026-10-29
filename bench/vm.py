# The test script of one benchmark run. bench/run.py starts the test driver with HYPRMASQ_BENCH_OUT set to the
# run's directory on the host, and the two talk through files there:
#
#   ready.json      written here once the task is set up and hyprmasq-mcp listens on vsock port 5000
#   agent-done      written by run.py when the agent has finished
#   vm-result.json  written here after grading; the driver then stops the VM
#
# TASK_DIR and VARIANT, the task's variant or "", are set by default.nix before this file runs. The task's setup.py defines prepare() and collect(), and
# may define HYPRLAND_ENV, variables for Hyprland's environment and so for every app it starts, and during(stop),
# which runs in a thread while the agent works, until the threading.Event stop is set: the user's own activity.
# With HYPRMASQ_BENCH_MEMORY_SECONDS, as calibrate.py sets it, the VM's memory in use is sampled that often while the
# agent works, and vm-result.json holds its peak; not for a task with during(), since the driver's shell takes one
# command at a time.
import base64
import json
import os
import shlex
import threading
import time

OUT = os.environ["HYPRMASQ_BENCH_OUT"]
AGENT_TIMEOUT = float(os.environ.get("HYPRMASQ_BENCH_TIMEOUT", "2400"))
MEMORY_SECONDS = float(os.environ.get("HYPRMASQ_BENCH_MEMORY_SECONDS", "0"))
RUNTIME = "/run/user/1000"
MCP_PORT = 5000


def out_path(name):
    return os.path.join(OUT, name)


def write_json(name, data):
    with open(out_path(name), "w") as f:
        json.dump(data, f, indent=2)


def alice(command):
    return machine.succeed("su - alice -c " + shlex.quote(command))


def fetch(path, name):
    """Copies a file from the VM into the run's directory, if it exists."""
    status, data = machine.execute(f"test -f {shlex.quote(path)} && base64 -w0 {shlex.quote(path)}")
    if status == 0:
        with open(out_path(name), "wb") as f:
            f.write(base64.b64decode(data))


exec(open(os.path.join(TASK_DIR, "setup.py")).read())

machine.start()
machine.wait_for_unit("multi-user.target")
machine.succeed(f"install -d -o alice -g users -m 700 {RUNTIME}")
alice(
    f"XDG_RUNTIME_DIR={RUNTIME} HYPRLAND_HEADLESS_ONLY=1 {globals().get('HYPRLAND_ENV', '')} "
    "nohup Hyprland --config /etc/hyprmasq-bench/hyprland.lua > /tmp/hyprmasq-bench-hyprland.log 2>&1 &"
)
machine.wait_until_succeeds(f"test -f {RUNTIME}/hypr/*/hyprland.lock", timeout=120)
instance = machine.succeed(f"basename $(dirname $(ls {RUNTIME}/hypr/*/hyprland.lock | head -1))").strip()
display = machine.succeed(f"sed -n '2p' {RUNTIME}/hypr/{instance}/hyprland.lock").strip()
machine.succeed(f"echo {instance} > /tmp/hyprmasq-bench-instance && chmod 644 /tmp/hyprmasq-bench-instance")
ipc = f"XDG_RUNTIME_DIR={RUNTIME} HYPRLAND_INSTANCE_SIGNATURE={instance}"
client_env = f"XDG_RUNTIME_DIR={RUNTIME} WAYLAND_DISPLAY={display}"


def hypr(command):
    return alice(f"{ipc} hyprctl {command}").strip()


def clients():
    return json.loads(hypr("-j clients"))


def window(predicate):
    return next((c for c in clients() if predicate(c)), None)


def move(address, workspace):
    hypr("dispatch " + shlex.quote(
        f'hl.dsp.window.move({{ window = "address:{address}", workspace = "name:{workspace}", follow = false }})'
    ))
    wait_for(f"{address} on {workspace}", lambda: window(
        lambda c: c["address"] == address and c["workspace"]["name"] == workspace) is not None)


def settle(description, *, initial_delay, interval, fields, predicate=lambda c: True, timeout=120):
    """Wait for two matching client snapshots, preserving the task's delays and fields."""
    def state():
        return [tuple(c[field]["name"] if field == "workspace" else c[field] for field in fields)
                for c in clients() if predicate(c)]

    def settled():
        before = state()
        time.sleep(interval)
        return before == state()

    time.sleep(initial_delay)
    wait_for(description, settled, timeout=timeout)


def snapshot():
    return {
        "workspace": json.loads(hypr("-j activeworkspace"))["id"],
        "window": json.loads(hypr("-j activewindow")).get("address"),
    }


def wait_for(description, predicate, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.5)
    raise AssertionError("Timed out: " + description)


hypr("output create headless HYPRMASQ-BENCH")
wait_for("headless monitor", lambda: any(m["name"] == "HYPRMASQ-BENCH" for m in json.loads(hypr("-j monitors"))))


def shoot_windows(prefix):
    """Screenshots of every window, and of the screen, into the run's directory."""
    for c in clients():
        path = f"/tmp/hyprmasq-bench-shot-{c['address']}.png"
        reply = json.loads(hypr(f"-j hyprmasq screenshot {c['address']} {path}"))
        if reply.get("ok"):
            fetch(path, f"{prefix}-{c['class'] or 'window'}-{c['address']}.png")
    alice(f"{client_env} grim /tmp/hyprmasq-bench-screen.png")
    fetch("/tmp/hyprmasq-bench-screen.png", f"{prefix}-screen.png")


alice(
    f"nohup /etc/hyprmasq-bench/events {RUNTIME}/hypr/{instance}/.socket2.sock /tmp/hyprmasq-bench-events.log "
    "> /dev/null 2>&1 &"
)

# The user's own work stays on the visible workspace for the whole run: a terminal that redraws every second.
alice(f"{client_env} nohup foot -T user-terminal top -d 1 > /tmp/hyprmasq-bench-foot.log 2>&1 &")
wait_for("user terminal", lambda: any(c["title"] == "user-terminal" for c in clients()))

task_window = prepare()

alice(
    f"nohup socat VSOCK-LISTEN:{MCP_PORT},fork,reuseaddr EXEC:/etc/hyprmasq-bench/mcp "
    "> /tmp/hyprmasq-bench-socat.log 2>&1 &"
)
machine.wait_until_succeeds(f"ss -H -l --vsock | grep -q ':{MCP_PORT}'", timeout=30)

before = snapshot()
shoot_windows("start")
vm_time = float(machine.succeed("date +%s.%N"))
write_json("ready.json", {
    "vm_time": vm_time,
    "host_time": time.time(),
    "user": before,
    "task_window": task_window,
    "clients": clients(),
})

stop_user = threading.Event()
user_thread = None
if "during" in globals():
    user_thread = threading.Thread(target=during, args=(stop_user,), daemon=True)
    user_thread.start()



def memory_in_use():
    """The VM's memory in use, in MiB: what it could not free for an app, page cache and buffers aside."""
    info = {line.split(":")[0]: int(line.split()[1]) for line in machine.succeed("cat /proc/meminfo").splitlines()}
    return info["MemTotal"] // 1024, (info["MemTotal"] - info["MemAvailable"]) // 1024


memory = []
next_sample = 0
deadline = time.monotonic() + AGENT_TIMEOUT
while not os.path.exists(out_path("agent-done")):
    if time.monotonic() > deadline:
        machine.log("the agent did not finish in time")
        break
    if MEMORY_SECONDS and not user_thread and time.monotonic() >= next_sample:
        memory.append(memory_in_use())
        next_sample = time.monotonic() + MEMORY_SECONDS
    time.sleep(0.5)

stop_user.set()
if user_thread:
    user_thread.join(timeout=30)
after = snapshot()
shoot_windows("end")
result = {
    "user_before": before,
    "user_after": after,
    "user_same_at_end": before == after,
    "clients": clients(),
    "task": collect(),
}
if memory:
    result["memory"] = {"total_mib": memory[0][0], "peak_used_mib": max(used for _, used in memory),
                        "samples": len(memory)}
for path, name in [
    ("/tmp/hyprmasq-bench-hyprland.log", "vm-hyprland.log"),
    ("/tmp/hyprmasq-bench-mcp.log", "vm-mcp.log"),
    ("/tmp/hyprmasq-bench-socat.log", "vm-socat.log"),
    ("/tmp/hyprmasq-bench-events.log", "vm-events.log"),
]:
    fetch(path, name)
write_json("vm-result.json", result)
