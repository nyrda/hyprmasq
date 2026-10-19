import json
import os
from pathlib import Path
import subprocess
import tempfile

with tempfile.TemporaryDirectory() as tmp:
    mock = Path(tmp) / "hyprctl"
    mock.write_text("""#!/bin/sh
if [ "$1" = hyprmasq ]; then
    if [ "$SCENARIO" = launch-failed ]; then echo 'plugin unloaded'; exit 1; fi
    if [ "$SCENARIO" = launch-refused ]; then echo 'launch refused'; exit 0; fi
    printf '%s' "$4" > "$TOKEN_FILE"
    echo 'launched pid 123'
else
    case $SCENARIO in
        invalid) echo 'plugin unloaded' ;;
        empty) : ;;
        query-failed) echo '{"windows":[]}'; exit 1 ;;
        *) printf '{"windows":[{"token":"%s","address":"0xabc"}]}\\n' "$(cat "$TOKEN_FILE")" ;;
    esac
fi
""")
    mock.chmod(0o755)
    env = dict(os.environ, PATH=tmp + ":" + os.environ["PATH"], TOKEN_FILE=tmp + "/token")
    for scenario, message in [("invalid", "invalid JSON"), ("empty", "invalid JSON"), ("query-failed", "cannot query"), ("launch-failed", "launch failed"), ("launch-refused", "launch failed")]:
        env["SCENARIO"] = scenario
        p = subprocess.run(["hyprmasq-launch", "--timeout", "5", "true"], env=env, capture_output=True, text=True, timeout=10)
        assert p.returncode == 1 and not p.stdout and message in p.stderr, (scenario, p)
        assert "parse error" not in p.stderr, p
    env["SCENARIO"] = "success"
    p = subprocess.run(["hyprmasq-launch", "--workspace", "-A.z_09", "--timeout", "00008", "--", "true"], env=env, capture_output=True, text=True, timeout=10)
    assert p.returncode == 0 and p.stdout == "0xabc\n" and not p.stderr, p
