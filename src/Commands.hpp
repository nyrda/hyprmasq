#pragma once

#include <hyprland/src/SharedDefs.hpp>

#include <string>
#include <string_view>

namespace hyprmasq {
    inline constexpr std::string_view VERSION = HYPRMASQ_VERSION;
    // Increases with every incompatible change to the commands.
    inline constexpr int INTERFACE = 4;

    // Handler for `hyprctl hyprmasq ...`.
    std::string handleCommand(eHyprCtlOutputFormat format, std::string request);
}
