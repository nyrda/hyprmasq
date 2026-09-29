#pragma once

#include <hyprland/src/desktop/DesktopTypes.hpp>

#include <functional>
#include <string>
#include <string_view>

class CWLSurfaceResource;

namespace hyprmasq {
    // Formats a window the way `hyprctl clients` prints its `address` field.
    std::string windowAddress(const PHLWINDOW& window);

    // Accepts `0x...` or `address:0x...`. Returns nullptr for unknown or unmapped windows.
    PHLWINDOW windowFromAddress(std::string_view address);

    // Calls fn for the window's surface, its subsurfaces and its popups with their subsurfaces.
    void forEachSurface(const PHLWINDOW& window, const std::function<void(SP<CWLSurfaceResource>)>& fn);
}
