#include "Windows.hpp"

#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/desktop/view/Popup.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/protocols/XDGShell.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>

#include <algorithm>
#include <cmath>
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

    bool resizing(const PHLWINDOW& window) {
        const auto surface = window->wlSurface() ? window->wlSurface()->resource() : nullptr;
        if (!surface)
            return false;
        // Within a logical pixel: a buffer of odd width at scale 2 is half a pixel wide.
        const auto differs = [](const Vector2D& a, const Vector2D& b) { return std::abs(a.x - b.x) >= 1 || std::abs(a.y - b.y) >= 1; };
        // Hyprland takes the size it configured an X11 window to last as reported once the window commits.
        if (window->m_isX11)
            return differs(window->m_reportedSize, window->m_pendingReportedSize) && differs(surface->m_current.size, window->m_pendingReportedSize);
        if (!window->m_xdgSurface)
            return false;
        // Hyprland notes the size of each configure, floored, and applies it as the acked size with the first commit after
        // the window acks it. A window that acks a configure that keeps its size need not commit, so its content counts
        // too: the xdg window geometry when the window sets one, otherwise its surface.
        const auto     goal = window->size(Desktop::View::IGeometric::GEOMETRIC_GOAL);
        const Vector2D size{std::floor(std::max(0.0, goal.x)), std::floor(std::max(0.0, goal.y))};
        const auto     geometry = window->m_xdgSurface->m_current.geometry;
        const auto     content  = geometry.w > 0 && geometry.h > 0 ? geometry.size() : surface->m_current.size;
        return differs(surface->m_current.ackedSize, size) && differs(content, size);
    }
}
