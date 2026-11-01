#include "Suspension.hpp"
#include "Hooks.hpp"
#include "PrivateAccess.hpp"

#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/protocols/XDGShell.hpp>

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

        // The hooked function, see SPinned at the end of this file.
        using SetSuspended                        = uint32_t (CXDGToplevelResource::*)(bool);
        HookFn<SetSuspended> originalSetSuspended = nullptr;
        // Every held toplevel. An entry expires with its last hold.
        std::vector<std::weak_ptr<SHeld>> held;

        bool told(const CXDGToplevelResource& toplevel) {
            return std::ranges::find(toplevel.m_pendingApply.states, XDG_TOPLEVEL_STATE_SUSPENDED) != toplevel.m_pendingApply.states.end();
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
            return originalSetSuspended(self, suspend);
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
        if (window && window->m_isMapped && surface && surface->m_mapped)
            t->setSuspeneded(true);
        else
            t->m_pendingApply.states.push_back(XDG_TOPLEVEL_STATE_SUSPENDED);
    }

    CAwake::CAwake(const PHLWINDOW& window) {
        const auto toplevel = window && !window->m_isX11 && window->m_xdgSurface ? window->m_xdgSurface->m_toplevel.lock() : nullptr;
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
        originalSetSuspended = hook<SetSuspended>(handle, "_ZN20CXDGToplevelResource13setSuspenededEb", "CXDGToplevelResource::setSuspeneded(bool)", &setSuspended);
    }
}

template struct hyprmasq::SPinned<hyprmasq::suspension::SetSuspended, &CXDGToplevelResource::setSuspeneded>;
