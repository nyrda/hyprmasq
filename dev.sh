#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
cmd="${1:-build}"
if [[ $# -gt 0 ]]; then shift; fi
case "$cmd" in
    build) exec nix develop -c make -j"$(nproc)" "$@" ;;
    vm-test) exec nix build .#checks.x86_64-linux.vm-test -L "$@" ;;
    container-test) exec nix build .#checks.x86_64-linux.container-test -L "$@" ;;
    gate)
        features=" $(nix config show system-features) "
        if [[ $features == *" uid-range "* && $features == *" gpu "* ]] && compgen -G '/dev/dri/renderD*' > /dev/null; then
            echo "gate: the container test" >&2
            exec nix build .#checks.x86_64-linux.container-test -L "$@"
        fi
        echo "gate: the VM test, as this machine lacks a render node or the uid-range and gpu system features" >&2
        exec nix build .#checks.x86_64-linux.vm-test -L "$@"
        ;;
    vm-test-interactive)
        nix build .#checks.x86_64-linux.vm-test.driverInteractive "$@"
        exec ./result/bin/nixos-test-driver
        ;;
    *) printf 'Usage: %s {build|gate|vm-test|container-test|vm-test-interactive}\n' "$0" >&2; exit 2 ;;
esac
