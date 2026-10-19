#!/usr/bin/env bash
# Boots the benchmark VM for a run directory and waits until it is ready or the driver exits.
#   smoke.sh DRIVER DIR [CID]
set -euo pipefail
if [[ "${1:-}" == --help ]]; then
    echo "Usage: bench/smoke.sh DRIVER DIR [CID]"
    echo "Boot a benchmark VM without an agent; replaces DIR and waits for ready.json."
    exit 0
fi
driver=$1 dir=$2 cid=${3:-4242}
rm -rf "$dir" && mkdir -p "$dir/tmp" "$dir/driver-out"
HYPRMASQ_BENCH_OUT=$dir TMPDIR=$dir/tmp HYPRMASQ_BENCH_CID=$cid HYPRMASQ_BENCH_TIMEOUT=3600 \
    setsid nohup "$driver/bin/nixos-test-driver" -o "$dir/driver-out" > "$dir/driver.log" 2>&1 &
pid=$!
echo "$pid" > "$dir/driver.pid"
while [ ! -f "$dir/ready.json" ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "driver exited"; grep -E "Error|Traceback|Timed out" "$dir/driver.log" | tail -5; exit 1
    fi
    sleep 2
done
echo ready
