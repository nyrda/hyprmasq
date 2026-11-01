#include "Idle.hpp"
#include "Hooks.hpp"
#include "PrivateAccess.hpp"

#include <hyprland/src/protocols/IdleNotify.hpp>

namespace hyprmasq::idle {
    namespace {
        // The hooked function, see SPinned at the end of this file.
        using SetInhibit                      = void (CIdleNotifyProtocol::*)(bool);
        HookFn<SetInhibit> originalSetInhibit = nullptr;

        void setInhibit(CIdleNotifyProtocol* self, bool inhibited) {
            if (inhibitedOf(*self) == inhibited)
                return;
            originalSetInhibit(self, inhibited);
        }
    }

    void init(void* handle) {
        originalSetInhibit = hook<SetInhibit>(handle, "_ZN19CIdleNotifyProtocol10setInhibitEb", "CIdleNotifyProtocol::setInhibit(bool)", &setInhibit);
    }
}

template struct hyprmasq::SPinned<hyprmasq::idle::SetInhibit, &CIdleNotifyProtocol::setInhibit>;
