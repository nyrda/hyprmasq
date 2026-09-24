#pragma once

#include <hyprland/src/SharedDefs.hpp>

#include <string>

namespace hyprmasq {
    // Handler for `hyprctl hyprmasq ...`.
    std::string handleCommand(eHyprCtlOutputFormat format, std::string request);
}
