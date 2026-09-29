#include "Windows.hpp"

#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/desktop/view/Popup.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>

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

    void forEachSurface(const PHLWINDOW& window, const std::function<void(SP<CWLSurfaceResource>)>& fn) {
        auto visit = [&](SP<CWLSurfaceResource> s) {
            if (s)
                s->breadthfirst([&](SP<CWLSurfaceResource> child, const Vector2D&, void*) { fn(child); }, nullptr);
        };
        visit(window->wlSurface()->resource());
        if (window->m_popupHead)
            window->m_popupHead->breadthfirst([&](SP<Desktop::View::CPopup> p, void*) { visit(p->resource()); }, nullptr);
    }
}
