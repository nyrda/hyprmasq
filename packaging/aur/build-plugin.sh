#!/usr/bin/env bash
# Builds the prebuilt plugin of hyprmasq-plugin-bin for one Hyprland build: build-plugin.sh VERSION DAY OUT, as root in
# the archlinux/archlinux base-devel image that `builds.py snapshot VERSION` names, with the DAY it names, from the
# commit that the repository this script is in has checked out. The image moves to the Arch archive's snapshot of DAY,
# the first that has that Hyprland and so the oldest userland that runs it, so that the plugin needs no newer glibc or
# libstdc++ than any of them has. It builds with Arch's makepkg flags.
set -euo pipefail
version=$1 day=$2 out=$(realpath -m "$3")
repo=$(cd "$(dirname "$0")/../.." && pwd)
cat > /etc/pacman.d/mirrorlist <<EOF
Server = https://umea.archive.pkgbuild.com/repos/$day/\$repo/os/\$arch
Server = https://archive.archlinux.org/repos/$day/\$repo/os/\$arch
EOF
retry() {
  local attempt
  for attempt in 1 2 3; do
    "$@" && return 0
    [[ $attempt == 3 ]] && return 1
    sleep $((5 * attempt))
  done
}
# -yy: an image from after the snapshot would keep its newer databases with -y.
retry pacman -Syyuu --noconfirm
retry pacman -S --noconfirm --needed base-devel git hyprland libei libglvnd libpng libxcb libxkbcommon
installed=$(pacman -Q hyprland | cut -d' ' -f2)
if [[ $installed != "$version" ]]; then
  echo "build-plugin.sh: the snapshot of $day has hyprland $installed, not $version" >&2
  exit 1
fi
tree=$(mktemp -d)
git -C "$repo" archive HEAD | tar -x -C "$tree"
# shellcheck source=/dev/null
. /etc/makepkg.conf
export CFLAGS CXXFLAGS LDFLAGS
make -C "$tree" -j"$(nproc)" hyprmasq.so
strip --strip-unneeded "$tree/hyprmasq.so"
install -Dm755 "$tree/hyprmasq.so" "$out"
echo "build-plugin.sh: $out for Hyprland $version on the userland of $day"
