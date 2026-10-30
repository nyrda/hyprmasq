#pragma once

#include <hyprland/src/desktop/DesktopTypes.hpp>
#include <hyprland/src/helpers/math/Math.hpp>

#include <functional>
#include <string>
#include <string_view>
#include <vector>

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

    // True while Hyprland renders the window on a monitor that shows frames, so that its render loop sends the window frame
    // callbacks. A window on a visible workspace gets none while DPMS has turned its monitor off, or while the session is
    // inactive, as on another VT: Hyprland renders nothing then, so the window counts as hidden. Main thread only.
    bool rendered(const PHLWINDOW& window);

    // Calls fn for the window's surface, its subsurfaces and its popups with their subsurfaces.
    void forEachSurface(const PHLWINDOW& window, const std::function<void(SP<CWLSurfaceResource>)>& fn);

    // A mapped popup of a Wayland window, as the renderer draws it: the box of its surfaces with a buffer, in window-local
    // logical pixels, where pointer input reaches it too, and whether it asked for a grab.
    struct SPopupBox {
        CBox box;
        bool grab = false;
    };
    // The window's popups in the order of its popup tree, parents before their children. None for an X11 window, whose
    // menus are windows of their own. Main thread only.
    std::vector<SPopupBox> popupBoxes(const PHLWINDOW& window);

    // True while the window has not caught up with a size Hyprland gave it: it has not yet committed a frame after
    // taking the size, and its content does not have that size either. Its content then shows a frame of another size.
    // Main thread only.
    bool resizing(const PHLWINDOW& window);
}
