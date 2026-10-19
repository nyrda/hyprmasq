#pragma once

#include <hyprland/src/desktop/DesktopTypes.hpp>
#include <hyprland/src/helpers/math/Math.hpp>

#include <functional>
#include <string>
#include <string_view>

class CWLSurfaceResource;
struct wl_client;

namespace hyprmasq {
    // Formats a window the way `hyprctl clients` prints its `address` field.
    std::string windowAddress(const PHLWINDOW& window);

    // A window address given as `0x...` or `address:0x...`, as `0x...`.
    std::string_view normalizeAddress(std::string_view address);

    // Accepts `0x...` or `address:0x...`. Returns nullptr for unknown or unmapped windows.
    PHLWINDOW windowFromAddress(std::string_view address);

    // The geometry offset of an xdg window, which the window-local coordinates of screenshots and emulated input leave
    // out: the offset of its content within its surface. 0 for an X11 window.
    Vector2D geometryOffset(const PHLWINDOW& window);

    // The smallest box that holds both boxes.
    CBox unite(const CBox& a, const CBox& b);

    // True if focus is on a surface of the client, or of the window's Wayland connection.
    bool sharesConnection(wl_client* client, const WP<CWLSurfaceResource>& focus);
    bool sharesConnection(const PHLWINDOW& window, const WP<CWLSurfaceResource>& focus);

    // Calls fn for the window's surface, its subsurfaces and its popups with their subsurfaces.
    void forEachSurface(const PHLWINDOW& window, const std::function<void(SP<CWLSurfaceResource>)>& fn);

    // True while the window has not caught up with a size Hyprland gave it: it has not yet committed a frame after
    // taking the size, and its content does not have that size either. Its content then shows a frame of another size.
    // Main thread only.
    bool resizing(const PHLWINDOW& window);
}
