#pragma once
#include "Windows.hpp"

#include <cstdint>
#include <optional>
#include <vector>

struct wl_client;

// XWayland routes keys by X input focus and pointer events by X stacking order, not by the Wayland surface
// that receives them. These helpers adjust both around emulated input and put them back afterwards.
namespace hyprmasq::x11 {
    void init(void* handle);
    void shutdown();

    // The Wayland client acting as the window's X server: Hyprland's XWayland, an xwayland-satellite, or null.
    wl_client* server(const PHLWINDOW& window);
    // Whether a Wayland client is an X server: Hyprland's XWayland or an xwayland-satellite.
    bool serves(wl_client* client);

    // Moves X input focus to an XWayland window until release(). False if the X server cannot be reached.
    bool focus(const PHLWINDOW& window);
    // Restores the X input focus that focus() replaced, unless something else has moved it since. With a window,
    // only releases a focus held for that window.
    void release(const PHLWINDOW& window = nullptr);
    // Answers an activation request of an XWayland window that was refused, so that the app does not keep considering
    // itself active.
    void refuseActivation(const PHLWINDOW& window);

    struct SStack {
        std::vector<uint32_t> before; // root's children, bottom to top
        std::vector<uint32_t> raised; // bottom to top
    };
    // Raises an XWayland window and its menus above every other X window. Nullopt if the X server cannot be reached.
    std::optional<SStack> raise(const PHLWINDOW& window);
    // Returns the raised windows to their places.
    void lower(const SStack& stack);
}
