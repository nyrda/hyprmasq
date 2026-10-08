#pragma once

#include <optional>
#include <sys/types.h>

// Reads of /proc/<pid>/stat, the kernel's own data about a process, which never wait for the process, unlike its
// environment or command line, which are read from its memory. The plugin, running in Hyprland, may read them also when
// a client in a sandbox may not.
namespace hyprmasq::process {
    // The parent of a process, or 0 when it cannot be read.
    pid_t parent(pid_t pid);
    // When a process started, in clock ticks since boot, which tells a process from a later one with the same pid, or
    // none when it cannot be read.
    std::optional<unsigned long long> started(pid_t pid);

}
