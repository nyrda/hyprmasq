#pragma once

// Hyprland checks its idle inhibitors as any window maps, unmaps or takes the focus, and each check passes the result
// to CIdleNotifyProtocol::setInhibit, which restarts the idle timers of the notifications that obey inhibitors even when
// the result has not changed. An idle client takes that for activity: hypridle runs its on-resume commands, such as
// turning the monitors on again, as the plugin launches or closes a window on a hidden workspace while the user is away.
// The hook passes a call on only when it changes whether an inhibitor holds, so idle timers restart for user input and
// for inhibitors that come or go, as before. The hook is installed for the plugin's lifetime, see hooks::idle: maps and
// unmaps come at any time. Main thread only.
namespace hyprmasq::idle {
    // Installs the hook on CIdleNotifyProtocol::setInhibit, unless another plugin hooks it.
    void init(void* handle);
    void shutdown();
}
