#pragma once

#include "Error.hpp"

#include <expected>
#include <string>
#include <sys/types.h>
#include <vector>

// Applications that hyprmasq-mcp's launch starts, and their windows. The plugin starts the command itself, through
// Hyprland's executor and /bin/sh, and remembers the process. When Hyprland creates a window, before it maps it, the
// plugin follows the parents of the window's process, which /proc/<pid>/stat gives without waiting for any process,
// up to a launched one. Such a window belongs to the launch, also one of a child process, as a browser that its
// launcher script starts without exec. Within a minute of the launch, the plugin tags the window and registers a window
// rule for that tag, which puts the window silently on the launch's workspace as it maps, and removes both once it has
// mapped or is destroyed. Main thread only.
namespace hyprmasq::launch {
    void init();
    void shutdown();

    // Starts a shell command line on the named workspace, under the token that windows() names its windows by.
    std::expected<pid_t, SError> start(const std::string& workspace, const std::string& token, const std::string& command);

    struct SLaunched {
        std::string address;  // as hyprctl clients prints it
        std::string stableId; // as hyprctl clients prints it, which tells the window from a later one at its address
        std::string token;
    };
    // The mapped windows of launches, in the order Hyprland created them.
    std::vector<SLaunched> windows();
}
