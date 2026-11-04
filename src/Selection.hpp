#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

struct wl_client;

// The agent's clipboard and primary selection: what the apps of driven windows copy and select through emulated input.
// Hyprland takes a selection from any app, so the plugin wraps the set_selection handlers of the data devices and primary
// selection devices of every app that emulated input reaches, and keeps a selection that the agent's input made from
// Hyprland: the user's selections stay as they are, and the apps that emulated input reaches see the agent's, also
// across apps. For Hyprland's XWayland, which bridges X selections itself, it keeps the agent's X selections from the
// user's. The file descriptors of a paste go from the destination to the source through Hyprland's own offers, which the
// compositor never reads or writes. See "Clipboard and selections" in docs/automation.md.
namespace hyprmasq::selection {
    // Defines the hooks of the X window manager's bridge of X selections, which hooks::x11Input installs with input to X11
    // windows, and hooks::x11Selection while an X selection stays the agent's, and listens for changes of the real focus
    // and selections.
    void init(void* handle);
    // Emulated input reaches the client now. Wraps the set_selection handlers of its data devices and primary selection
    // devices, also of ones created since the last call, and counts the input as the agent's, the last input to the client
    // until the user's own comes. When the input is about to give emulated input the client's keyboard or pointer, as
    // keys and presses, which can paste, do, the client also gets the agent's selections, none before the agent has
    // made any, unless it has them already: call it before the input and its enters.
    void emulatedInput(wl_client* client, bool keyboard, bool pointer);
    // The user's own input reaches the client, or the real keyboard focus moves to it. Call before the input arrives: when
    // the client made the agent's clipboard, that copy ends, and its source gets a cancel.
    void userInput(wl_client* client);
    // The agent's clipboard becomes the text, which the plugin holds, for the agent's paste. The agent's primary selection
    // stays: GTK drops a text selection whose source gets a cancel.
    // Returns the serial of the text, which rises with every text the agent's clipboard becomes.
    uint64_t setAgentText(std::string text);
    // The text that the agent's clipboard is, if it still is one: its serial, how many pastes asked for it, how many of
    // them have ended, and how many of these got all of it.
    struct SAgentText {
        uint64_t serial = 0;
        bool     held   = false;
        size_t   asked = 0, ended = 0, complete = 0;
    };
    SAgentText agentText();
    // A socket closed, which may end the agent's say over new X selections, see agentSelects() in Selection.cpp. Looks
    // once the current batch of events is handled, when the socket is gone from Eis.cpp's list.
    void socketClosed();
    // Emulated input gave the client's keyboard or pointer back to the user's window there: if the client has the agent's
    // selections, it gets the user's back.
    void restore(wl_client* client);
    // Gives the user's selections back to the apps that have the agent's and the real focus, cancels the agent's
    // selections, and puts Hyprland's handlers back. Call after shutdownEis, before unhookAll.
    void shutdown();
}
