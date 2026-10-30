#include "Idle.hpp"
#include "Hooks.hpp"
#include "PrivateAccess.hpp"

#include <hyprland/src/protocols/IdleNotify.hpp>

namespace hyprmasq::idle {
    namespace {
        void* originalSetInhibit = nullptr;

        void setInhibit(CIdleNotifyProtocol* self, bool inhibited) {
            if (inhibitedOf(*self) == inhibited)
                return;
            using Fn = void (*)(CIdleNotifyProtocol*, bool);
            reinterpret_cast<Fn>(originalSetInhibit)(self, inhibited);
        }
    }

    void init(void* handle) {
        originalSetInhibit = hook(handle, "CIdleNotifyProtocol::setInhibit", reinterpret_cast<void*>(&setInhibit));
    }
}
