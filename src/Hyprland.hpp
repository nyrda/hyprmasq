#pragma once

// What differs between the Hyprland releases that the plugin builds against, see HyprlandVersion.hpp.
#include "HyprlandVersion.hpp"

#if HYPRMASQ_HYPRLAND >= 57
#include <hyprland/src/desktop/view/window/Window.hpp>
#include <hyprland/src/ipc/s1/S1.hpp>
#include <hyprland/src/workspace/HLWorkspace.hpp>
#else
#include <hyprland/src/SharedDefs.hpp>
#include <hyprland/src/desktop/Workspace.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/protocols/XDGShell.hpp>
#endif
#include <hyprland/src/debug/log/Logger.hpp>
#include <hyprland/src/desktop/view/Popup.hpp>

#include <cstdint>
#include <format>
#include <optional>
#include <string>
#include <utility>

namespace hyprmasq {
    // hyprctl's request format and a command's reply, which 0.57 can answer later through a promise.
#if HYPRMASQ_HYPRLAND >= 57
    using IPC::Socket1::eOutputFormat;
    using IPC::Socket1::FORMAT_JSON;
    using IPC::Socket1::SResponse;
#else
    using eOutputFormat = eHyprCtlOutputFormat;
    using SResponse     = std::string;
#endif
}

namespace hyprmasq::hl {
    template <typename... Args>
    void log(Hyprutils::CLI::eLogLevel level, std::format_string<Args...> fmt, Args&&... args) {
#if HYPRMASQ_HYPRLAND >= 57
        Log::logger->log(level, "hyprmasq", "{}", std::format(fmt, std::forward<Args>(args)...));
#else
        Log::logger->log(level, "[hyprmasq] {}", std::format(fmt, std::forward<Args>(args)...));
#endif
    }

    inline bool mapped(const PHLWINDOW& w) {
#if HYPRMASQ_HYPRLAND >= 57
        return w->mapped();
#else
        return w->m_isMapped;
#endif
    }

    inline bool mapped(const SP<Desktop::View::CPopup>& popup) {
#if HYPRMASQ_HYPRLAND >= 57
        return popup->mapped();
#else
        return popup->m_mapped;
#endif
    }

    inline bool isX11(const PHLWINDOW& w) {
#if HYPRMASQ_HYPRLAND >= 57
        return w->backend().isX11();
#else
        return w->m_isX11;
#endif
    }

    inline pid_t pid(const PHLWINDOW& w) {
#if HYPRMASQ_HYPRLAND >= 57
        return w->backend().pid();
#else
        return w->getPID();
#endif
    }

    inline uint64_t stableID(const PHLWINDOW& w) {
#if HYPRMASQ_HYPRLAND >= 57
        return w->metadata().stableID();
#else
        return w->m_stableID;
#endif
    }

    // An xdg_toplevel parent or WM_TRANSIENT_FOR.
    inline PHLWINDOW parent(const PHLWINDOW& w) {
#if HYPRMASQ_HYPRLAND >= 57
        return w->backend().parent();
#else
        return w->parent();
#endif
    }

    inline bool pinned(const PHLWINDOW& w) {
#if HYPRMASQ_HYPRLAND >= 57
        return !!(w->m_state & Desktop::View::WINDOW_STATE_PINNED);
#else
        return w->m_pinned;
#endif
    }

    inline SP<Desktop::View::CPopup> popupHead(const PHLWINDOW& w) {
#if HYPRMASQ_HYPRLAND >= 57
        return w->popupHead();
#else
        return w->m_popupHead;
#endif
    }

    inline size_t popupTreeSize(const PHLWINDOW& w) {
#if HYPRMASQ_HYPRLAND >= 57
        return w->popupTreeSize();
#else
        return w->m_popupHead ? w->m_popupHead->popupTreeCount() : 0;
#endif
    }

    // The xdg window geometry of a Wayland window, in surface-local logical pixels, while it has an xdg surface.
    inline std::optional<CBox> xdgGeometry(const PHLWINDOW& w) {
#if HYPRMASQ_HYPRLAND >= 57
        if (w->backend().isX11() || !w->backend().valid())
            return std::nullopt;
        return w->backend().geometry().box;
#else
        if (w->m_isX11 || !w->m_xdgSurface)
            return std::nullopt;
        return w->m_xdgSurface->m_current.geometry;
#endif
    }

    // Surface pixels per logical pixel of an X11 window, more than 1 when Hyprland forces zero scaling for XWayland.
    inline double x11SurfaceScale(const PHLWINDOW& w) {
#if HYPRMASQ_HYPRLAND >= 57
        return w->backend().surfaceScale();
#else
        return w->m_X11SurfaceScaledBy;
#endif
    }

    // A workspace as a window rule or a dispatcher selects it: the name of a special or named workspace, and the
    // number of a numbered one.
    inline bool special(const PHLWORKSPACE& workspace) {
#if HYPRMASQ_HYPRLAND >= 57
        return workspace->type() == Workspace::eWorkspaceType::SPECIAL;
#else
        return workspace->m_isSpecialWorkspace;
#endif
    }

    inline std::string name(const PHLWORKSPACE& workspace) {
#if HYPRMASQ_HYPRLAND >= 57
        return workspace->addressableName();
#else
        return workspace->m_name;
#endif
    }

    inline std::optional<int64_t> number(const PHLWORKSPACE& workspace) {
#if HYPRMASQ_HYPRLAND >= 57
        if (const auto id = workspace->numberedID(); id && *id > 0)
            return *id;
#else
        if (workspace->m_id > 0 && !workspace->m_isSpecialWorkspace)
            return workspace->m_id;
#endif
        return std::nullopt;
    }
}
