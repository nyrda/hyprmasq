#include "Idle.hpp"
#include "Hooks.hpp"
#include "PrivateAccess.hpp"

#include <hyprland/src/protocols/IdleNotify.hpp>

#include <optional>

namespace hyprmasq::idle {
    namespace {
        // The hooked function, see SPinned at the end of this file.
        using SetInhibit                             = void (CIdleNotifyProtocol::*)(bool);
        HookFn<SetInhibit>        originalSetInhibit = nullptr;
        std::optional<CHookClaim> installed;

        void setInhibit(CIdleNotifyProtocol* self, bool inhibited) {
            if (inhibitedOf(*self) == inhibited)
                return;
            originalSetInhibit(self, inhibited);
        }
    }

    void init(void* handle) {
        hooks::idle.add(defineHook<SetInhibit, &setInhibit>(handle, "_ZN19CIdleNotifyProtocol10setInhibitEb", "CIdleNotifyProtocol::setInhibit(bool)", originalSetInhibit));
        installed = hooks::forLifetime(hooks::idle);
    }

    void shutdown() {
        installed.reset();
    }
}

template struct hyprmasq::SPinned<hyprmasq::idle::SetInhibit, &CIdleNotifyProtocol::setInhibit>;
