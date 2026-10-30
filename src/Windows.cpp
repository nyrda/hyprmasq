#include "Windows.hpp"
#include "FocusPolicy.hpp"

#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/desktop/view/Popup.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/output/Monitor.hpp>
#include <hyprland/src/protocols/XDGShell.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#include <hyprland/src/render/Renderer.hpp>

#include <algorithm>
#include <cmath>
#include <format>
#include <optional>

namespace hyprmasq {
    std::string windowAddress(const PHLWINDOW& window) {
        return std::format("0x{:x}", rc<uintptr_t>(window.get()));
    }

    std::string_view normalizeAddress(std::string_view address) {
        if (address.starts_with("address:"))
            address.remove_prefix(8);
        return address;
    }

    PHLWINDOW windowFromAddress(std::string_view address) {
        address = normalizeAddress(address);

        for (const auto& window : Desktop::windowState()->windows()) {
            if (window && window->m_isMapped && windowAddress(window) == address)
                return window;
        }

        return nullptr;
    }

    Vector2D geometryOffset(const PHLWINDOW& window) {
        return !window->m_isX11 && window->m_xdgSurface ? window->m_xdgSurface->m_current.geometry.pos() : Vector2D{};
    }

    CBox unite(const CBox& a, const CBox& b) {
        const double x = std::min(a.x, b.x), y = std::min(a.y, b.y);
        return {x, y, std::max(a.x + a.w, b.x + b.w) - x, std::max(a.y + a.h, b.y + b.h) - y};
    }

    bool sharesConnection(wl_client* client, const WP<CWLSurfaceResource>& focus) {
        auto surface = focus.lock();
        return surface && client && surface->client() == client;
    }

    bool sharesConnection(const PHLWINDOW& w, const WP<CWLSurfaceResource>& focus) {
        return w->resource() && sharesConnection(w->resource()->client(), focus);
    }

    bool rendered(const PHLWINDOW& window) {
        if (!g_pHyprRenderer->shouldRenderWindow(window) || !g_pCompositor->m_sessionActive)
            return false;
        // CMonitor::setDPMS clears m_dpmsStatus as its fade to black starts, and disables the output when it ends.
        const auto monitor = window->m_monitor.lock();
        return monitor && monitor->m_enabled && monitor->m_dpmsStatus;
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

    std::vector<SPopupBox> popupBoxes(const PHLWINDOW& window) {
        std::vector<SPopupBox> boxes;
        if (window->m_isX11 || !window->m_popupHead || !window->m_xdgSurface)
            return boxes;
        const auto geometry = geometryOffset(window);
        window->m_popupHead->breadthfirst(
            [&](SP<Desktop::View::CPopup> popup, void*) {
                const auto surface = popup->resource();
                if (!popup->m_mapped || !surface || !surface->m_mapped)
                    return;
                // At its parent's origin less the window's geometry offset, as the renderer and the plugin's pointer place it.
                const auto          origin = popup->coordsRelativeToParent() - geometry;
                std::optional<CBox> extents;
                surface->breadthfirst(
                    [&](SP<CWLSurfaceResource> s, const Vector2D& offset, void*) {
                        if (!s->m_current.texture || s->m_current.size.x < 1 || s->m_current.size.y < 1)
                            return;
                        const CBox box{origin + offset, s->m_current.size};
                        extents = extents ? unite(*extents, box) : box;
                    },
                    nullptr);
                if (extents)
                    boxes.push_back({.box = *extents, .grab = input::grabbing(surface)});
            },
            nullptr);
        return boxes;
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
