#pragma once

#include "Error.hpp"

#include <hyprland/src/desktop/DesktopTypes.hpp>

#include <cstdint>
#include <expected>
#include <filesystem>
#include <functional>
#include <memory>
#include <new>
#include <optional>
#include <string>
#include <utility>
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

    // An allocator that leaves the elements that a vector adds uninitialized, for pixels that a read fills at once: zeroing
    // the pixels of a large window first takes as long as reading them.
    template <typename T>
    struct SUninitialized : std::allocator<T> {
        template <typename U>
        struct rebind {
            using other = SUninitialized<U>;
        };
        SUninitialized() noexcept = default;
        template <typename U>
        SUninitialized(const SUninitialized<U>&) noexcept {}
        template <typename U>
        void construct(U* p) noexcept {
            ::new (static_cast<void*>(p)) U;
        }
        template <typename U, typename... A>
        void construct(U* p, A&&... args) {
            ::new (static_cast<void*>(p)) U(std::forward<A>(args)...);
        }
    };
    using Pixels = std::vector<uint8_t, SUninitialized<uint8_t>>;

    struct SImage {
        int    width  = 0;
        int    height = 0;
        Pixels rgba;      // premultiplied, top row first
        double scale = 1; // image pixels per logical pixel
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

    // A capture whose pixels the GPU is still reading back from its render, see startCapture().
    struct SPendingCapture;

    // Renders the window offscreen, whether or not it is on a visible workspace, and starts reading back the region, or
    // the whole window, without waiting for the GPU. Main thread only.
    std::expected<std::shared_ptr<SPendingCapture>, SError> startCapture(const PHLWINDOW& window, const SCaptureOptions& options = {});
    // Calls then with the capture once the GPU has read it back, from Hyprland's event loop, which goes on meanwhile, or
    // at the unload, which waits for the GPU. Main thread only, also for the capture's end, which unmaps its pixels: the
    // work of deferred::run() and of deferred::later() may hold it, see Deferred.hpp.
    void onCaptured(std::shared_ptr<SPendingCapture> capture, std::move_only_function<void(std::expected<std::shared_ptr<SPendingCapture>, SError>)> then);
    // Copies the images out of a capture that onCaptured() handed over, once. Safe on any thread, so that the copy of a
    // large window's pixels does not hold up Hyprland.
    std::expected<SCapture, SError> takePixels(SPendingCapture& capture);
    // The images once the GPU has read them back, which waits for it. Main thread only.
    std::expected<SCapture, SError> finishCapture(SPendingCapture& capture);
    // Finishes the captures that are being read back, and frees what captures keep for the next ones. Before
    // deferred::shutdown(), which answers their replies. Main thread only.
    void shutdownCaptures();
    // Encodes the image as a PNG, or writes its pixels as they are: straight-alpha RGBA, top row first, without a header.
    // A FIFO is written once a reader has opened it. Once stop, an eventfd or -1, is readable, a write that waits for a
    // FIFO's reader or for room in a pipe ends with code UNLOADED. A write that Hyprland's main thread waits for, see
    // deferred::waited(), writes only regular files, and refuses anything else with code UNSUPPORTED before it writes
    // to it. Safe on any thread.
    std::expected<void, SError> writePNG(SImage& image, const std::filesystem::path& output, int stop = -1, bool waited = false);
    std::expected<void, SError> writeRaw(SImage& image, const std::filesystem::path& output, int stop = -1, bool waited = false);
}
