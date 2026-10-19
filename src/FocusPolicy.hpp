#pragma once

// The Hyprland hooks of the input sockets. Most keep driven windows from taking the user's focus: popup grabs, drags,
// activation and focus on map are refused or run privately, and a lease keeps the emulated keyboard and pointer focus
// across changes of the real focus. The user's own input to a driven app ends its private drag and takes back the
// keyboard focus that a window kept for a shortcut. One hook follows window moves for the sockets' pointer regions.
// Main thread only.
namespace hyprmasq::input {
    // Installs the hooks, in the order that unhookAll() reverses, and listens for the user's keys.
    void initFocusPolicy(void* handle);
    void shutdownFocusPolicy();
}
