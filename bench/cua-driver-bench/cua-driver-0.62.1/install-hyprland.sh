#!/bin/bash
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
user=$(cat /opt/agentenv/session-user)
home=$(getent passwd "$user" | cut -d: -f6)

# Unpack cua-driver 0.62.1 executable
mkdir -p "$here/cua-driver"
tar -xzf "$here/cua-driver.tar.gz" -C "$here/cua-driver" cua-driver
rm -f "$here/cua-driver.tar.gz"

# Upgrade cua-hyprland-plugin on the Omarchy base from 0.47.4 to 0.62.1 using its official build kit
# against the base's pinned Hyprland 0.56.2-1 and GCC 16.1.1.
build_tools=(cmake ninja)
pacman --config "$here/arch-day.conf" -Sy --noconfirm
pacman --config "$here/arch-day.conf" -S --needed --noconfirm "${build_tools[@]}"

kit=$(mktemp -d)
trap 'rm -rf "$kit"' EXIT
plugin_stem="cua-hyprland-plugin-0.62.1-3f69010ccd901a04ecf7b009b2b95400c3cb4ab8"
mv "$here/plugin-src.tar.gz" "$kit/$plugin_stem.tar.gz"
tar -xzf "$here/plugin-kit.tar.gz" -C "$kit"
rm -f "$here/plugin-kit.tar.gz"
(cd "$kit" && sha256sum --check --quiet SHA256SUMS)
chown -R "$user:$user" "$kit"
runuser -u "$user" -- env HOME="$home" bash -c 'cd "$1" && makepkg --noconfirm' makepkg "$kit"
pacman -U --noconfirm "$kit"/cua-hyprland-plugin-*.pkg.tar.zst
pacman -Rns --noconfirm "${build_tools[@]}"
rm -f /var/lib/pacman/sync/core.db* /var/lib/pacman/sync/extra.db*
pacman -Q hyprland gcc cua-hyprland-plugin
