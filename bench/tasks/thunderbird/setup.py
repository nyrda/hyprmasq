# The Thunderbird task's hooks for bench/vm.py, which runs this file in its own namespace: prepare() sets the
# task up and returns the address of the window the agent starts with, collect() grades the run and gathers
# the server logs.

PROFILE = "/home/alice/.thunderbird-bench"
SPOOL = "/var/lib/hyprmasq-bench-smtp"
TB_ETC = "/etc/hyprmasq-bench/thunderbird"


def prepare():
    machine.wait_for_unit("dovecot.service")
    machine.wait_for_unit("hyprmasq-bench-smtp.service")
    for port in (993, 465, 587):
        machine.wait_for_open_port(port)

    # A fresh profile that trusts the test CA, so that Thunderbird starts at its account setup.
    alice(
        f"mkdir -p {PROFILE} && cp {TB_ETC}/user.js {PROFILE}/ && "
        f"certutil -N -d sql:{PROFILE} --empty-password && "
        f"certutil -A -n 'hyprmasq bench test CA' -t 'C,,' -i {TB_ETC}/ca.pem -d sql:{PROFILE}"
    )
    # The user started Thunderbird earlier on a workspace of its own, which is not the visible one.
    address = alice(
        f"{ipc} hyprmasq-launch --workspace mail --timeout 180 -- thunderbird --profile {PROFILE}"
    ).strip()

    # Thunderbird keeps drawing for a while after its window maps: the account setup loads in a tab. Wait
    # until its windows stop changing, so that every run starts from the same screen.
    def settled():
        state = [(c["address"], c["title"]) for c in clients() if c["class"] != "foot"]
        time.sleep(5)
        return state == [(c["address"], c["title"]) for c in clients() if c["class"] != "foot"]

    time.sleep(10)
    wait_for("Thunderbird to settle", settled, timeout=120)
    return address


def journal(unit):
    """The unit's journal as (VM time in seconds, message) pairs."""
    lines = machine.succeed(f"journalctl -u {unit} -o json --no-pager || true").splitlines()
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("MESSAGE")
        if isinstance(message, str):
            entries.append((int(entry["__REALTIME_TIMESTAMP"]) / 1e6, message))
    return entries


def collect():
    # The checker logs in to IMAP too; only logins before it are Thunderbird's.
    check_start = float(machine.succeed("date +%s.%N"))
    check = json.loads(
        machine.succeed(f"python3 {TB_ETC}/check.py {SPOOL} {TB_ETC}/ca.pem")
    )
    logins = [t for t, m in journal("dovecot") if "Logged in: user=<mara>" in m and t < check_start]
    status, sent = machine.execute(f"cat {SPOOL}/log.jsonl")
    submissions = [json.loads(l) for l in sent.splitlines() if l.strip()] if status == 0 else []
    fetch(f"{SPOOL}/1.eml", "sent-1.eml")
    for unit, name in (("dovecot", "vm-dovecot.log"), ("hyprmasq-bench-smtp", "vm-smtp.log")):
        with open(out_path(name), "w") as f:
            f.write("\n".join(f"{t:.3f} {m}" for t, m in journal(unit)))
    # Phase ends in VM time; run.py converts them with the clock offset in ready.json.
    return {
        "check": check,
        "events": {
            "first_imap_login": logins[0] if logins else None,
            "first_submission": submissions[0]["time"] if submissions else None,
            "submissions": [s["time"] for s in submissions],
        },
    }
