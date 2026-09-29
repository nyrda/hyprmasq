#pragma once

#include <hyprland/src/desktop/DesktopTypes.hpp>

#include <cstdint>
#include <expected>
#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace hyprmasq {
    // A rectangle in window-local logical pixels, the coordinates of emulated pointer input.
    struct SRegion {
        int x = 0, y = 0, width = 0, height = 0;
    };

    struct SCapture {
        int                  width  = 0;
        int                  height = 0;
        std::vector<uint8_t> rgba; // premultiplied, top row first
        double               scale = 1;
        // The part of the window captured, in logical pixels: the requested region cut to the window and widened to
        // whole device pixels, so it can be fractional at fractional scales. Pixel PX,PY lies at x + PX / scale.
        struct {
            double x = 0, y = 0, width = 0, height = 0;
        } region;
    };

    // Renders the window offscreen, whether or not it is on a visible workspace, and reads back the region, or the whole
    // window. Main thread only.
    std::expected<SCapture, std::string> captureWindow(const PHLWINDOW& window, std::optional<SRegion> region = std::nullopt);
    // Encodes the capture as a PNG. Safe on any thread.
    std::expected<void, std::string> writePNG(SCapture& capture, const std::filesystem::path& output);
}
