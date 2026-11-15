#!/usr/bin/env bash
# Checks that prebuilt plugins and tools load their libraries on a userland: check-userland.sh USERLAND FILE..., as root
# in an archlinux/archlinux container. A FILE is a plugin, or the .tar.gz of the tools that build-tools.sh writes.
# USERLAND is arch, the container's own current Arch, or omarchy-stable, the snapshot that Omarchy's stable channel
# installs from, which the container moves to and so must be from before it. The dynamic loader then resolves the
# libraries of each program and plugin and their symbol versions, as Hyprland or the shell would, without running
# Hyprland: a library that a newer userland dropped, or a newer glibc or libstdc++ than the userland has, fails it.
# hyprmasq-mcp also runs once, with --version.
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
on="$userland (hyprland $(pacman -Q hyprland | cut -d' ' -f2))"
status=0
check() {
  local out
  out=$(/usr/lib/ld-linux-x86-64.so.2 --list "$1" 2>&1) || true
  if grep -E 'not found|error' <<<"$out"; then
    echo "check-userland.sh: $1 does not load on $on" >&2
    status=1
  else
    echo "check-userland.sh: $1 loads its libraries on $on"
  fi
}
for file in "$@"; do
  if [[ $file != *.tar.gz ]]; then
    check "$file"
    continue
  fi
  dir=$(mktemp -d)
  tar -xzf "$file" -C "$dir"
  for program in "$dir"/*/*; do
    # hyprmasq-launch is a shell script, and LICENSE text.
    if [[ $(head -c4 "$program") == $'\x7fELF' ]]; then check "$program"; fi
  done
  mcp=("$dir"/*/hyprmasq-mcp)
  if ! "${mcp[0]}" --version; then
    echo "check-userland.sh: hyprmasq-mcp of $file does not run on $on" >&2
    status=1
  fi
done
exit $status
