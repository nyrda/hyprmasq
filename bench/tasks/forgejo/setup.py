# The Forgejo task's hooks for bench/vm.py, which runs this file in its own namespace before it starts Hyprland.
#
# The user's browser runs on their workspace with the notes page, where they type during the whole run (during()),
# through QEMU's keyboard, so as real input. A second window of the same browser, the agent's, is parked on the
# hidden workspace `web`. It is opened and moved like a user would, not with hyprmasq's launch, whose placement
# would take the user's window too.

# Chromium talks to the desktop portal over the session bus of alice's systemd user manager.
HYPRLAND_ENV = "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus XDG_CURRENT_DESKTOP=Hyprland"

FORGEJO = "http://git.example.test:3000"
PASSWORD = "Forgejo-Mara-2026"
STATE = "/var/lib/hyprmasq-bench-forgejo"
DASH = "/var/lib/hyprmasq-bench-dash"
NOTES = "/var/lib/hyprmasq-bench-notes"
ETC = "/etc/hyprmasq-bench/forgejo"
CHROMIUM = (
    "chromium --user-data-dir=/home/alice/.config/chromium-bench --no-first-run --no-default-browser-check "
    "--password-store=basic --disable-search-engine-choice-screen --ozone-platform=wayland"
)
# Cua's browser tools attach to Mara's own browser, which on Linux they ask to be started with
# --force-renderer-accessibility (Cua's docs/content/docs/cua-driver/guides/browsers.mdx, Attach to a logged-in
# profile), and hypruse and computer-use-linux read Chromium's tree only with it (vm.py's CHROMIUM_ACCESSIBILITY).
# hyprmasq reads no accessibility, so its runs keep the browser as it was.
if CHROMIUM_ACCESSIBILITY:
    CHROMIUM += " --force-renderer-accessibility"

# What the user typed, line by line, for collect().
TYPED = []
USER_LINES = [
    "Agenda: review of release 1.4",
    "Dark mode ships on Thursday.",
    "Search by tag needs one more test.",
    "Ask Jonas about the RSS feeds.",
    "Next meeting: Monday, 10:00.",
]


def api(method, path, body=None):
    """A request to Forgejo's API as Mara, from inside the VM."""
    data = f"-H 'Content-Type: application/json' -d {shlex.quote(json.dumps(body))}" if body is not None else ""
    out = machine.succeed(
        f"curl --fail -s -X {method} -u mara:{PASSWORD} -H 'Accept: application/json' {data} "
        f"{FORGEJO}/api/v1{path}"
    )
    return json.loads(out) if out.strip() else None


def add_file(path, text, message):
    api("POST", f"/repos/mara/website/contents/{path}", {
        "content": base64.b64encode(text.encode()).decode(),
        "message": message,
        "branch": "main",
    })


def chromium_windows():
    return [c for c in clients() if c["class"].lower().startswith("chromium")]


def prepare():
    machine.wait_for_unit("forgejo.service")
    for port in (3000, 8080, 8081):
        machine.wait_for_open_port(port)

    machine.succeed(
        "su -l forgejo -c 'GITEA_WORK_DIR=/var/lib/forgejo forgejo admin user create "
        f"--username mara --password {PASSWORD} --email mara@example.test --must-change-password=false'"
    )
    api("POST", "/user/repos", {
        "name": "website", "description": "Mara's website", "auto_init": True,
        "default_branch": "main", "readme": "Default",
    })
    add_file("docs/index.md", "# Docs\n\nRelease notes and guides for the website.\n", "Add the docs index")
    add_file("docs/release-notes-1.3.md", "# Release notes 1.3\n\nReleased 2026-02-10.\n", "Add release notes for 1.3")
    machine.succeed(f"install -d -m 0755 {STATE}")
    main = api("GET", "/repos/mara/website/branches/main")["commit"]["id"]
    machine.succeed(f"echo {shlex.quote(json.dumps({'main': main}))} > {STATE}/initial.json")
    token = api("POST", "/users/mara/tokens", {"name": "bench-check", "scopes": ["read:repository", "read:user"]})
    machine.succeed(f"echo {token['sha1']} > {STATE}/token && chmod 600 {STATE}/token")
    client = api("POST", "/user/applications/oauth2", {
        "name": "Release dashboard",
        "redirect_uris": ["http://dash.example.test:8080/callback"],
        "confidential_client": True,
    })
    machine.succeed(
        f"echo {shlex.quote(json.dumps({'client_id': client['client_id'], 'client_secret': client['client_secret']}))}"
        f" > {DASH}/client.json"
    )

    alice(f"mkdir -p ~/Documents && cp {ETC}/release-notes-1.4.md ~/Documents/")

    # The portal's services start from alice's systemd user manager, so they need the session's variables.
    alice(
        f"export {client_env} {HYPRLAND_ENV} HYPRLAND_INSTANCE_SIGNATURE={instance}; "
        "systemctl --user import-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP HYPRLAND_INSTANCE_SIGNATURE "
        "&& systemctl --user start hyprmasq-bench-session.target"
    )

    # The agent's window: the browser's first, moved to the hidden workspace `web`.
    alice(f"{client_env} {HYPRLAND_ENV} nohup {CHROMIUM} {FORGEJO}/ > /tmp/hyprmasq-bench-chromium.log 2>&1 &")
    wait_for("the browser", lambda: len(chromium_windows()) == 1, timeout=120)
    agent_window = chromium_windows()[0]["address"]
    move(agent_window, "web")

    # The user's window, on the visible workspace, with the focus in the notes.
    alice(f"{client_env} {HYPRLAND_ENV} {CHROMIUM} --new-window http://notes.example.test:8081/")
    wait_for("the user's browser window", lambda: len(chromium_windows()) == 2, timeout=60)
    user_window = next(c["address"] for c in chromium_windows() if c["address"] != agent_window)
    hypr("dispatch " + shlex.quote(f'hl.dsp.focus({{ window = "address:{user_window}" }})'))

    settle("the browser to settle", initial_delay=5, interval=3,
           fields=("address", "title", "workspace"))
    return agent_window


def during(stop):
    """The user types a line of notes every few seconds, as real keyboard input."""
    stop.wait(3)
    n = 0
    while not stop.is_set():
        line = USER_LINES[n % len(USER_LINES)] + "\n"
        machine.send_chars(line, delay=0.05)
        TYPED.append(line)
        n += 1
        stop.wait(6)


def collect():
    time.sleep(2)  # the page saves after each key
    check = json.loads(machine.succeed(
        f"python3 {ETC}/check.py {STATE} {DASH} {ETC}/release-notes-1.4.md"))
    status, saved = machine.execute(f"cat {NOTES}/notes.json")
    notes = json.loads(saved)["text"] if status == 0 else ""
    typed = "".join(TYPED)
    first_difference = next((i for i, (a, b) in enumerate(zip(notes, typed)) if a != b), min(len(notes), len(typed)))
    check["checks"]["user_notes"] = {
        "intact": notes == typed,
        "typed_chars": len(typed),
        "saved_chars": len(notes),
        "first_difference": None if notes == typed else first_difference,
    }
    with open(out_path("user-notes.txt"), "w") as f:
        f.write(f"typed:\n{typed}\n---\nsaved:\n{notes}\n")
    events = check.pop("events")
    return {"check": check, "events": events}
