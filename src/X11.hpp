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

    // While one lives, the functions here wait for the X server for at most 100 ms in all, or 10 ms for a second after
    // that ran out, until the X server answers in time again. Each of them opens one, so a caller only needs one around
    // several calls, such as those of one input frame.
    class CWaitBudget {
      public:
        CWaitBudget();
        ~CWaitBudget();
        CWaitBudget(const CWaitBudget&)            = delete;
        CWaitBudget& operator=(const CWaitBudget&) = delete;
    };

    // Hyprland's XWayland as a Wayland client, or null while there is none.
    wl_client* xwaylandClient();
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
        std::vector<uint32_t> before;  // root's children, bottom to top
        std::vector<uint32_t> lowered; // the windows that raise() put below the target, bottom to top
    };
    // Puts the X windows above an XWayland window below it, but for the menus of its app, so that pointer input reaches
    // the window and those menus. Nullopt if the X server cannot be reached.
    std::optional<SStack> raise(const PHLWINDOW& window);
    // Returns the windows that raise() moved to their places.
    void lower(const SStack& stack);

    // Whether the X focus that focus() holds for an XWayland window has to stay once input to it pauses: while its app
    // has a menu open, an override-redirect window of a menu's type, which X11 apps such as Chromium close once their
    // window loses the X focus.
    bool keepsFocus(const PHLWINDOW& window);

    // What Hyprland's window manager knows of an XWayland window that decides what its close dispatcher does: it asks a
    // window that takes WM_DELETE_WINDOW to close, and kills the X client of any other, which ends its app.
    struct SWindowKind {
        bool overrideRedirect = false; // the window manager does not manage it, as a menu, list or tooltip
        bool deleteWindow     = false; // WM_PROTOCOLS lists WM_DELETE_WINDOW
    };
    // Nullopt for a window that is not one of Hyprland's XWayland.
    std::optional<SWindowKind> kind(const PHLWINDOW& window);
}
