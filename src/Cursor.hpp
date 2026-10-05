#pragma once
#include "Windows.hpp"

#include <hyprutils/math/Vector2D.hpp>

// Virtual cursors show where emulated pointer input lands: a copy of the user's cursor in the configured color, and a
// ripple for each click. They are drawn over windows on visible workspaces and fade out once input stops, unless a focus
// lease keeps the emulated pointer on the window.
namespace hyprmasq::cursor {
    void init(void* handle);
    void shutdown();

    // position is window-local, in logical pixels.
    void show(const PHLWINDOW& window, const Hyprutils::Math::Vector2D& position, bool click);
}
