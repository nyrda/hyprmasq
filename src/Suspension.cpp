#include "Suspension.hpp"
#include "Hooks.hpp"
#include "PrivateAccess.hpp"

#include <hyprland/src/desktop/view/window/Window.hpp>
#include <hyprland/src/protocols/XDGShell.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>

#include <algorithm>
#include <vector>

namespace hyprmasq::suspension {
    // A held toplevel, which its holds share. Once the last one ends, it is told what Hyprland told it last.
    struct SHeld {
        WP<CXDGToplevelResource> toplevel;
        bool                     suspended = false; // what Hyprland told it last
        ~SHeld();
    };

    namespace {
        // The xdg_wm_base version that has XDG_TOPLEVEL_STATE_SUSPENDED. Hyprland tells older toplevels nothing.
        constexpr int SUSPENDED_SINCE = 6;

        void*         originalSetSuspended = nullptr;
        // Every held toplevel. An entry expires with its last hold.
        std::vector<std::weak_ptr<SHeld>> held;

        bool told(const CXDGToplevelResource& toplevel) {
            return std::ranges::find(toplevel.m_pendingApply.states, XDG_TOPLEVEL_STATE_SUSPENDED) != toplevel.m_pendingApply.states.end();
        }

        // The xdg toplevel of a Wayland window, through the xdg role of its surface.
        SP<CXDGToplevelResource> toplevelOf(const PHLWINDOW& window) {
            const auto surface = window && !window->backend().isX11() ? window->resource() : nullptr;
            if (!surface || !surface->m_role || surface->m_role->role() != SURFACE_ROLE_XDG_SHELL)
                return nullptr;
            const auto xdg = sc<CXDGSurfaceRole*>(surface->m_role.get())->m_xdgSurface.lock();
            return xdg ? xdg->m_toplevel.lock() : nullptr;
        }

        std::shared_ptr<SHeld> heldOf(const CXDGToplevelResource* toplevel) {
            std::erase_if(held, [](const auto& entry) { return entry.expired(); });
            for (const auto& entry : held) {
                auto locked = entry.lock();
                if (locked && !locked->toplevel.expired() && locked->toplevel.get() == toplevel)
                    return locked;
            }
            return nullptr;
        }

        // Hyprland's CWindow::setSuspended calls this when its own state of the window changes. A held toplevel is not
        // told, and the state is kept for when the last hold ends.
        uint32_t setSuspended(CXDGToplevelResource* self, bool suspend) {
            if (const auto entry = heldOf(self)) {
                entry->suspended = suspend;
                suspend          = false;
            }
            using Fn = uint32_t (*)(CXDGToplevelResource*, bool);
            return reinterpret_cast<Fn>(originalSetSuspended)(self, suspend);
        }
    }

    // The entry has expired by now, so the hook passes the call on.
    SHeld::~SHeld() {
        const auto t = toplevel.lock();
        if (!t || !suspended || told(*t))
            return;
        // A configure goes out to a mapped window. One that is unmapped gets the state with its next configure, as it
        // maps again. Hyprland emits a window's unmap, in which a hold can end, as a session's does when its socket
        // closes with it, before it clears m_isMapped, but a toplevel that stays, unmapped by a commit without a buffer,
        // has its xdg surface unmapped by then.
        const auto window  = t->m_window.lock();
        const auto surface = t->m_owner.lock();
        if (window && window->mapped() && surface && surface->m_mapped)
            t->setSuspeneded(true);
        else
            t->m_pendingApply.states.push_back(XDG_TOPLEVEL_STATE_SUSPENDED);
    }

    CAwake::CAwake(const PHLWINDOW& window) {
        const auto toplevel = toplevelOf(window);
        if (!toplevel || !wireOf(*toplevel) || wireOf(*toplevel)->version() < SUSPENDED_SINCE || (m_held = heldOf(toplevel.get())))
            return;
        const bool suspended = told(*toplevel);
        // Before the entry exists, so that the hook passes the call on. The configure goes out once Hyprland has
        // handled the current batch of events.
        if (suspended)
            toplevel->setSuspeneded(false);
        m_held            = std::make_shared<SHeld>();
        m_held->toplevel  = toplevel;
        m_held->suspended = suspended;
        held.push_back(m_held);
    }

    void init(void* handle) {
        originalSetSuspended = hook(handle, "CXDGToplevelResource::setSuspeneded", reinterpret_cast<void*>(&setSuspended));
    }
}
