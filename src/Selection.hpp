#pragma once

struct wl_client;

// The agent's clipboard and primary selection: what the apps of driven windows copy and select through emulated input.
// Hyprland takes a selection from any app, so the plugin wraps the set_selection handlers of the data devices and primary
// selection devices of every app that emulated input reaches, and keeps a selection that the agent's input made from
// Hyprland: the user's selections stay as they are, and the apps that emulated input reaches see the agent's, also
// across apps. For Hyprland's XWayland, which bridges X selections itself, it keeps the agent's X selections from the
// user's. The file descriptors of a paste go from the destination to the source through Hyprland's own offers, which the
// compositor never reads or writes. See "Clipboard and selections" in docs/automation.md.
namespace hyprmasq::selection {
    // Hooks the X window manager's bridge of X selections and listens for changes of the real focus and selections.
    void init(void* handle);
    // Emulated input reaches the client now. Wraps the set_selection handlers of its data devices and primary selection
    // devices, also of ones created since the last call, and counts the input as the agent's, the last input to the client
    // until the user's own comes. When the input is about to give emulated input the client's keyboard or pointer, as
    // keys and presses, which can paste, do, the client also gets the agent's selections, none before the agent has
    // made any, unless it has them already: call it before the input and its enters.
    void emulatedInput(wl_client* client, bool keyboard, bool pointer);
    // The user's own input reaches the client, or the real keyboard focus moves to it.
    void userInput(wl_client* client);
    // Emulated input gave the client's keyboard or pointer back to the user's window there: if the client has the agent's
    // selections, it gets the user's back.
    void restore(wl_client* client);
    // Gives the user's selections back to the apps that have the agent's and the real focus, cancels the agent's
    // selections, and puts Hyprland's handlers back. Call after shutdownEis, before unhookAll.
    void shutdown();
}
