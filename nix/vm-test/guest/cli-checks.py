import json
import re
import subprocess
import sys

version = sys.argv[1]

def run(tool, *args, status=0, message=None):
    p = subprocess.run([tool, *args], text=True, capture_output=True, timeout=10)
    assert p.returncode == status, (tool, args, p)
    if status == 0:
        assert not p.stderr, p
    else:
        assert message in p.stderr and "--help" in p.stderr, p
    return p

for tool in ("hyprmasq-input", "hyprmasq-launch"):
    for flag in ("-h", "--help"):
        p = run(tool, flag)
        assert "Usage:" in p.stdout and "Options:" in p.stdout and "Exit codes:" in p.stdout, p
        assert all(len(line) <= 80 for line in p.stdout.splitlines()), p.stdout
        for section in p.stdout.split("\n\n"):
            if not section.startswith(("Options:", "Actions, in order:", "Key tags in type TEXT,")):
                continue
            columns = []
            for line in section.splitlines()[1:]:
                row = re.match(r"^  \S.*? {2,}(\S.*)$", line)
                if row:
                    columns.append(row.start(1))
                elif line.startswith("   "):
                    columns.append(len(line) - len(line.lstrip()))
            assert len(columns) >= 2 and len(set(columns)) == 1, (section, columns)
    assert run(tool, "--version").stdout == f"{tool} {version}\n"

for args, message in [
    ([], "--socket PATH is required"),
    (["--socket"], "--socket needs a value"),
    (["--budget"], "--budget needs a value"),
    (["--locale"], "--locale needs a value"),
    (["--socket", "/tmp/hm-cli-no-socket"], "at least one action is required"),
    (["--socket", "/tmp/hm-cli-no-socket", "key"], "expected key CODE"),
    (["--socket", "/tmp/hm-cli-no-socket", "move", "nan", "0"], "expected move X Y"),
    (["--budget", "-1"], "--budget takes"),
    (["--bad-option"], "invalid option"),
]:
    p = run("hyprmasq-input", "--json", *args, status=2, message=message)
    result = json.loads(p.stdout)
    assert result["error"]["code"] == "invalid_arguments" and result["delivery"] == "none", result
    assert result["done"] == 0 and not result["ok"], result

for args, message in [
    ([], "a command is required"),
    (["--workspace"], "--workspace needs a value"),
    (["--timeout"], "--timeout needs a value"),
    (["--workspace", "bad+name", "true"], "workspace name"),
    (["--unknown"], "invalid option"),
] + [(["--timeout", value, "true"], "positive whole number") for value in ("", "0", "000", "-1", "1.5", "abc", "2147483648", "9" * 80)]:
    assert not run("hyprmasq-launch", *args, status=2, message=message).stdout
