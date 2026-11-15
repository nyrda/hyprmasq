#!/usr/bin/env bash
# Builds the prebuilt MCP server and tools of hyprmasq-bin: build-tools.sh VERSION DAY OUT, as root in the
# archlinux/archlinux base-devel image that `builds.py snapshot` names for the oldest DAY of the plugins, from the commit
# that the repository this script is in has checked out. The image moves to the Arch archive's snapshot of DAY, the
# oldest userland that runs a plugin, so that the tools need no newer glibc than any of them has. makepkg builds the
# hyprmasq PKGBUILD of packaging/aur there, as the AUR's hyprmasq builds on a user's machine, with its tests, and OUT
# gets what it installs: a .tar.gz of the directory hyprmasq-VERSION-x86_64.
set -euo pipefail
version=$1 day=$2 out=$(realpath -m "$3")
repo=$(cd "$(dirname "$0")/../.." && pwd)
echo "Server = https://archive.archlinux.org/repos/$day/\$repo/os/\$arch" > /etc/pacman.d/mirrorlist
# -yy: an image from after the snapshot would keep its newer databases with -y.
pacman -Syyuu --noconfirm
# The PKGBUILD's depends and makedepends, without hyprmasq-plugin, which the tools do not need to build.
pacman -S --noconfirm --needed base-devel ffmpeg git go jq libei libxkbcommon
work=$(mktemp -d)
sed "s/^pkgver=.*/pkgver=$version/" "$repo/packaging/aur/hyprmasq/PKGBUILD" > "$work/PKGBUILD"
# makepkg takes the release archive from beside the PKGBUILD rather than downloading it.
git -C "$repo" archive --prefix="hyprmasq-$version/" HEAD | gzip -n > "$work/hyprmasq-$version.tar.gz"
id builder > /dev/null 2>&1 || useradd -m builder
chown -R builder "$work"
# makepkg refuses to run as root. --nodeps: hyprmasq-plugin comes from the AUR. --noarchive leaves the files in pkg/.
(cd "$work" && sudo -u builder makepkg --nodeps --noarchive --skipchecksums)
pkg=$work/pkg/hyprmasq
dir=$work/hyprmasq-$version-x86_64
mkdir "$dir"
cp "$pkg"/usr/bin/* "$pkg/usr/lib/hyprmasq/hyprmasq-reaper" "$pkg/usr/share/licenses/hyprmasq/LICENSE" "$dir/"
mkdir -p "$(dirname "$out")"
tar -C "$work" --sort=name --owner=0 --group=0 --numeric-owner -czf "$out" "$(basename "$dir")"
tar -tvzf "$out"
echo "build-tools.sh: $out on the userland of $day"
