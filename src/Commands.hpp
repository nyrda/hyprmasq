#pragma once

#include <hyprland/src/ipc/s1/S1.hpp>

#include <string>
#include <string_view>

namespace hyprmasq {
    inline constexpr std::string_view VERSION = HYPRMASQ_VERSION;
    // Increases with every incompatible change to the commands.
    inline constexpr int INTERFACE = 5;

    // Handler for `hyprctl hyprmasq ...`.
    IPC::Socket1::SResponse handleCommand(IPC::Socket1::eOutputFormat format, std::string request);
}
