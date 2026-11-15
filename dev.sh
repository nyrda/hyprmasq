#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
cmd="${1:-build}"
if [[ $# -gt 0 ]]; then shift; fi
# --hyprland 0.56 builds and tests against the older Hyprland release of HYPRLAND_VERSION instead of the pinned one.
suffix="" shell=""
if [[ ${1:-} == --hyprland ]]; then
    case "${2:-}" in
        0.57) ;;
        0.56) suffix="-hyprland056" shell="#hyprland056" ;;
        *) echo "dev.sh: --hyprland takes 0.57 or 0.56" >&2; exit 2 ;;
    esac
    shift 2
fi
case "$cmd" in
    build) exec nix develop ".$shell" -c make -j"$(nproc)" "$@" ;;
    vm-test) exec nix build ".#checks.x86_64-linux.vm-test$suffix" -L "$@" ;;
    container-test) exec nix build ".#checks.x86_64-linux.container-test$suffix" -L "$@" ;;
    gate)
        features=" $(nix config show system-features) "
        if [[ $features == *" uid-range "* && $features == *" gpu "* ]] && compgen -G '/dev/dri/renderD*' > /dev/null; then
            echo "gate: the container test" >&2
            exec nix build ".#checks.x86_64-linux.container-test$suffix" -L "$@"
        fi
        echo "gate: the VM test, as this machine lacks a render node or the uid-range and gpu system features" >&2
        exec nix build ".#checks.x86_64-linux.vm-test$suffix" -L "$@"
        ;;
    vm-test-interactive)
        nix build ".#checks.x86_64-linux.vm-test$suffix.driverInteractive" "$@"
        exec ./result/bin/nixos-test-driver
        ;;
    *) printf 'Usage: %s {build|gate|vm-test|container-test|vm-test-interactive} [--hyprland 0.57|0.56] [nix options]\n' "$0" >&2; exit 2 ;;
esac
