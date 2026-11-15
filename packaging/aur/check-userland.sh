#!/usr/bin/env bash
# Checks that prebuilt plugins load their libraries on a userland: check-userland.sh USERLAND PLUGIN.so..., as root in
# an archlinux/archlinux container. USERLAND is arch, the container's own current Arch, or omarchy-stable, the snapshot
# that Omarchy's stable channel installs from, which the container moves to and so must be from before it. The dynamic
# loader then resolves each plugin's libraries and their symbol versions as Hyprland would, without loading Hyprland:
# a library that a newer userland dropped, or a newer glibc or libstdc++ than the userland has, fails it.
set -euo pipefail
userland=$1
shift
# shellcheck disable=SC2016 # pacman expands $repo and $arch.
case $userland in
  arch) ;;
  omarchy-stable) echo 'Server = https://stable-mirror.omarchy.org/$repo/os/$arch' > /etc/pacman.d/mirrorlist ;;
  *) echo "check-userland.sh: unknown userland $userland" >&2; exit 2 ;;
esac
# -yy: the databases of an image from after the snapshot are newer than its own.
pacman -Syyuu --noconfirm
pacman -S --noconfirm --needed hyprland libei libglvnd libpng libxcb libxkbcommon
status=0
for plugin in "$@"; do
  out=$(/usr/lib/ld-linux-x86-64.so.2 --list "$plugin" 2>&1) || true
  if grep -E 'not found|error' <<<"$out"; then
    echo "check-userland.sh: $plugin does not load on $userland (hyprland $(pacman -Q hyprland | cut -d' ' -f2))" >&2
    status=1
  else
    echo "check-userland.sh: $plugin loads its libraries on $userland"
  fi
done
exit $status
