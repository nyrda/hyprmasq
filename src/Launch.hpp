#pragma once

#include "Error.hpp"

#include <hyprland/src/desktop/DesktopTypes.hpp>

#include <expected>
#include <string>
#include <sys/types.h>
#include <vector>

// Applications that hyprmasq-mcp's launch starts, and their windows. The plugin starts the command itself, through
// Hyprland's executor and /bin/sh, under hyprmasq-reaper, and remembers the process. As the subreaper of the command's
// processes, hyprmasq-reaper becomes the parent of those whose parents exit, as the GUI of an app whose launcher forks
// it away and exits, and stays until the last of them has exited. When Hyprland creates a window, before it maps it,
// the plugin follows the parents of the window's process, which /proc/<pid>/stat gives without waiting for any process,
// up to a launched one. Such a window belongs to the launch, also one of a child process, as a browser that its
// launcher script starts without exec, or of a process that the launch's processes left behind. Without
// hyprmasq-reaper next to the plugin, in ../libexec of its directory or on PATH, the command runs on its own, and the
// windows of such a process are lost. Within a minute of the launch, the plugin tags the window and registers a window
// rule for that tag, which puts the window silently on the launch's workspace as it maps, and removes both once it has
// mapped or is destroyed. A window that emulated input opened, see placement.hpp, it puts on the workspace of the window
// it came from the same way, also when no launch started its app. Main thread only.
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

    // Puts a window that has not mapped yet silently on the workspace as it maps, floating at the size it asks for, with
    // a tag and a rule, or keeps or moves the placement that an earlier call made. A window that a launch places stays
    // on the launch's workspace, and floats there. False when the workspace has no name that a rule can give.
    bool placeOn(const PHLWINDOW& window, const PHLWORKSPACE& workspace);
    // Takes back what placeOn did for a window that has not mapped yet.
    void withdraw(const PHLWINDOW& window);

    // True for a window that the plugin placed silently, for as long as it lives.
    bool placed(const PHLWINDOW& window);
}
