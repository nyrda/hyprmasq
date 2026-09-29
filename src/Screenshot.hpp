#pragma once

#include <hyprland/src/desktop/DesktopTypes.hpp>

#include <cstdint>
#include <expected>
#include <filesystem>
#include <string>
#include <vector>

namespace hyprmasq {
    struct SCapture {
        int                  width  = 0;
        int                  height = 0;
        std::vector<uint8_t> rgba; // premultiplied, top row first
    };

    // Renders the window offscreen, whether or not it is on a visible workspace. Main thread only.
    std::expected<SCapture, std::string> captureWindow(const PHLWINDOW& window);
    // Encodes the capture as a PNG. Safe on any thread.
    std::expected<void, std::string> writePNG(SCapture& capture, const std::filesystem::path& output);
}
