#pragma once
#include "Windows.hpp"

#include <cstdint>
#include <optional>
#include <string>

// Hyprland opens a new window on the active workspace and focuses it. A window that an app opens in response to
// emulated input opens on the workspace of the window it came from instead, without the focus, unless the user's own
// input to that window or app came later, before the app created the window; a line break that the user types does
// not count. The plugin decides before Hyprland reads the window's rules, at a Wayland window's first commit and again
// when the window maps, once its parent is known, by the input up to the first commit, or up to the map when nothing
// before the first commit can have opened it, and places it with the tag and rule of launches, see launch.hpp. See "New
// windows" in docs/usage.md.
// Main thread only.
namespace hyprmasq::placement {
    void init(void* handle);
    void shutdown();

    // Whether a key press can open a window: Enter, a key held with Ctrl, Alt or Super, or any key but one that types,
    // edits or moves in text, as a character, Space, Backspace or an arrow. Modifiers alone open none. Clicks, touches
    // and tablet tips are triggers too: input that typing into a text field is not.
    bool triggers(uint32_t keycode, bool shortcut);
    // Emulated input reached the window; trigger tells whether it can open a window.
    void emulatedInput(const PHLWINDOW& window, bool trigger);

    // The window that a new window came from, as the plugin found it when the new window mapped: its parent, or the
    // window of its app that got emulated input last.
    struct SSource {
        std::string  address;
        PHLWINDOWREF window = {};    // while it lives, which tells it from a later window at its address
        bool         placed = false; // on that window's workspace, without the focus
        std::string  reason = {};    // why not: disabled, user_input or workspace
    };
    std::optional<SSource> source(const PHLWINDOW& window);
}
