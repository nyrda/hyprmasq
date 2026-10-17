#pragma once

#include "Error.hpp"

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

    struct SCaptureOptions {
        std::optional<SRegion> region;
        std::optional<int>     maxPixels;      // fit the image within this many pixels on its long side
        bool                   popups = false; // capture the window's popups also where they reach outside it
        // Also read a detail image from the same render, when the image has fewer pixels than one per logical pixel or
        // than the monitor's, whichever are fewer: the area at that scale.
        bool detail = false;
    };

    struct SImage {
        int                  width  = 0;
        int                  height = 0;
        std::vector<uint8_t> rgba;      // premultiplied, top row first
        double               scale = 1; // image pixels per logical pixel
        // The part of the window captured, in logical pixels: the requested region cut to the window, or to the window
        // and its popups, and widened to whole image pixels, so it can be fractional. Pixel PX,PY lies at
        // x + PX / scale, y + PY / scale.
        struct {
            double x = 0, y = 0, width = 0, height = 0;
        } region;
        // The max pixels of a request that reads the area at this scale again, or none for the monitor's scale.
        std::optional<int> maxPixels;
    };

    struct SCapture {
        SImage image;
        // The detail image, both read from one render, so they show the same frame.
        std::optional<SImage> detail;
    };

    // Renders the window offscreen, whether or not it is on a visible workspace, and reads back the region, or the whole
    // window. Main thread only.
    std::expected<SCapture, SError> captureWindow(const PHLWINDOW& window, const SCaptureOptions& options = {});
    // Encodes the image as a PNG, or writes its pixels as they are: straight-alpha RGBA, top row first, without a header.
    // Safe on any thread.
    std::expected<void, std::string> writePNG(SImage& image, const std::filesystem::path& output);
    std::expected<void, std::string> writeRaw(SImage& image, const std::filesystem::path& output);
}
