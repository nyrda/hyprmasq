#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
cmd="${1:-build}"
if [[ $# -gt 0 ]]; then shift; fi
case "$cmd" in
    build) exec nix develop -c make -j"$(nproc)" "$@" ;;
    vm-test) exec nix build .#checks.x86_64-linux.vm-test -L "$@" ;;
    vm-test-interactive)
        nix build .#checks.x86_64-linux.vm-test.driverInteractive "$@"
        exec ./result/bin/nixos-test-driver
        ;;
    *) printf 'Usage: %s {build|vm-test|vm-test-interactive}\n' "$0" >&2; exit 2 ;;
esac
