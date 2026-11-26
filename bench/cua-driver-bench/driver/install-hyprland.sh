#!/bin/bash
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
user=$(cat /opt/agentenv/session-user)
home=$(getent passwd "$user" | cut -d: -f6)

# Ensure build/runtime dependencies for hyprmasq.so, hyprmasq-input, hyprmasq-launch and grim are present
# from the exact same 2026/08/08 Arch Linux Archive snapshot that Omarchy's base recipe pins.
needed=(libei grim jq wtype make pkgconf libpng libdrm pixman libglvnd libxkbcommon libxcb)
missing=()
for pkg in "${needed[@]}"; do
    if ! pacman -Q "$pkg" >/dev/null 2>&1; then
        missing+=("$pkg")
    fi
done
if [ "${#missing[@]}" -gt 0 ]; then
    pacman --config "$here/arch-day.conf" -Sy --noconfirm
    pacman --config "$here/arch-day.conf" -S --needed --noconfirm "${missing[@]}"
    rm -f /var/lib/pacman/sync/core.db* /var/lib/pacman/sync/extra.db*
fi

# Build hyprmasq.so directly against Omarchy's exact installed Hyprland 0.56.2-1 headers and GCC 16.1.1,
# matching how recipe.sh builds cua-hyprland-plugin.so, so __hyprland_api_get_hash() matches 100%.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

tar -xzf "$here/hyprmasq-src.tar.gz" -C "$tmp"
srcdir=$(echo "$tmp"/hyprmasq-*)
make -C "$srcdir" -j"$(nproc)" hyprmasq.so

install -d /usr/lib/hyprmasq /usr/local/bin
install -m 755 "$srcdir/hyprmasq.so" /usr/lib/hyprmasq/hyprmasq.so

# Install prebuilt tools (hyprmasq-input, hyprmasq-launch, hyprmasq-mcp, hyprmasq-reaper) from v0.5.1.
tools_tmp=$(mktemp -d)
trap 'rm -rf "$tmp" "$tools_tmp"' EXIT
tar -xzf "$here/hyprmasq-tools.tar.gz" -C "$tools_tmp"
for bin in hyprmasq-input hyprmasq-launch hyprmasq-mcp hyprmasq-reaper; do
    install -m 755 "$tools_tmp"/hyprmasq-*/"$bin" "/usr/local/bin/$bin"
    ln -sf "/usr/local/bin/$bin" "/usr/bin/$bin"
done
ln -sf /usr/local/bin/hyprmasq-reaper /usr/lib/hyprmasq/hyprmasq-reaper
rm -f "$here/hyprmasq-src.tar.gz" "$here/hyprmasq-tools.tar.gz"

# Replace agent-env's Hyprland drop-in so Hyprland loads hyprmasq.so instead of cua-hyprland-plugin.so.
cat > "$home/.config/hypr/agentenv.lua" <<'LUA'
-- agent-env's Hyprland drop-in for hyprmasq: loads hyprmasq.so in place of cua-hyprland-plugin.so.
hl.env("GDK_SCALE", "1")
hl.plugin.load("/usr/lib/hyprmasq/hyprmasq.so")
hl.config({
  plugin = {
    hyprmasq = {
      virtual_cursor = false,
      place_new_windows = true,
    },
  },
  cursor = { inactive_timeout = 1 },
  input = { kb_rules = "evdev", kb_model = "pc105", kb_layout = "us", kb_variant = "", kb_options = "",
            numlock_by_default = false },
})
pcall(require, "hypr.agentenv-display")
LUA
chown "$user:$user" "$home/.config/hypr/agentenv.lua"
chmod 644 "$home/.config/hypr/agentenv.lua"
