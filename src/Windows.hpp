#pragma once

#include <hyprland/src/desktop/DesktopTypes.hpp>

#include <string>
#include <string_view>

namespace hyprmasq {
    // Formats a window the way `hyprctl clients` prints its `address` field.
    std::string windowAddress(const PHLWINDOW& window);

    // Accepts `0x...` or `address:0x...`. Returns nullptr for unknown or unmapped windows.
    PHLWINDOW windowFromAddress(std::string_view address);
}
