#pragma once

#include <hyprland/src/desktop/DesktopTypes.hpp>

#include <expected>
#include <filesystem>
#include <string>

namespace hyprmasq {
    struct SScreenshotInfo {
        int width  = 0;
        int height = 0;
    };

    // Renders the window offscreen, whether or not it is on a visible workspace, and writes a PNG.
    std::expected<SScreenshotInfo, std::string> screenshotWindow(const PHLWINDOW& window, const std::filesystem::path& output);
}
