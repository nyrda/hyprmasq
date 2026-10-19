#!/usr/bin/env bash
# Run every benchmark unit test, including Calc's headless LibreOffice cases. No agents or VMs.
set -euo pipefail
if [[ "${1:-}" == --help ]]; then
    echo "Usage: bench/test.sh"
    echo "Run all bench unit tests, including the Nix Calc checker, without agents or VMs."
    exit 0
fi
cd "$(dirname "$0")/.."
for test in bench/*_test.py bench/tasks/*/*_test.py; do
    if [[ "$test" != bench/tasks/calc/check_test.py ]]; then
        python3 "$test"
    fi
done
nix-build bench/tasks/calc/check-test.nix --no-out-link
