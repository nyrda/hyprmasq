#pragma once

#include <hyprland/src/helpers/memory/Memory.hpp>

class CWLSurfaceResource;

// The Hyprland hooks of the input sockets, in the groups hooks::input and, for X11 windows, hooks::x11Input, which the
// sockets install while they are in use, see SSession::claimHooks. Most keep driven windows from taking the user's
// focus: popup grabs, drags, activation and focus on map are refused or run privately, and a lease keeps the emulated
// keyboard and pointer focus across changes of the real focus. The user's own input to a driven app ends its private
// drag and takes back the keyboard focus that a window kept for a shortcut. One hook follows window moves for the
// sockets' pointer regions. Main thread only.
namespace hyprmasq::input {
    // Whether the surface is that of an xdg_popup that asked for a grab, which a popup does that takes the pointer and
    // keys while it is open, such as a menu or a dropdown's list, and a tooltip does not. Also when the plugin refused it.
    // Known only for popups that asked while hooks::input was installed.
    bool grabbing(const SP<CWLSurfaceResource>& surface);

    // Defines the hooks, adds them to their groups, and listens for the user's keys.
    void initFocusPolicy(void* handle);
    void shutdownFocusPolicy();
}
