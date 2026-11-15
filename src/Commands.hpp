#pragma once

#include "Hyprland.hpp"

#include <string>
#include <string_view>

namespace hyprmasq {
    inline constexpr std::string_view VERSION = HYPRMASQ_VERSION;
    // Increases with every incompatible change to the commands.
    inline constexpr int INTERFACE = 5;

    // Handler for `hyprctl hyprmasq ...`.
    SResponse handleCommand(eOutputFormat format, std::string request);
}
