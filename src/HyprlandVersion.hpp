#pragma once

// The plugin builds against two Hyprland releases, the one flake.lock pins and the one before it (HYPRLAND_VERSION
// names both), and against Hyprland main. HYPRMASQ_HYPRLAND is the minor version of the headers at hand: 57 for 0.57,
// 56 for 0.56. Hyprland main builds as the pinned release until it changes what the plugin uses, and then as the next
// minor version, which the first header it changed tells apart here. What differs goes through the helpers in
// Hyprland.hpp, or else behind #if HYPRMASQ_HYPRLAND in the file that needs it. See "Two Hyprland releases" and
// "Hyprland main" in docs/development.md.
#if __has_include(<hyprland/src/desktop/view/window/Window.hpp>)
#define HYPRMASQ_HYPRLAND 57
#else
#define HYPRMASQ_HYPRLAND 56
#endif
