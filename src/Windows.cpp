#include "Windows.hpp"

#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/desktop/view/Window.hpp>

#include <format>

namespace hyprmasq {
    std::string windowAddress(const PHLWINDOW& window) {
        return std::format("0x{:x}", rc<uintptr_t>(window.get()));
    }

    PHLWINDOW windowFromAddress(std::string_view address) {
        if (address.starts_with("address:"))
            address.remove_prefix(8);

        for (const auto& window : Desktop::windowState()->windows()) {
            if (window && window->m_isMapped && windowAddress(window) == address)
                return window;
        }

        return nullptr;
    }
}
