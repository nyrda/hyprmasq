#pragma once
#include <hyprland/src/helpers/memory/Memory.hpp>

#include <algorithm>
#include <string_view>
#include <utility>
#include <vector>

struct wl_client;

namespace hyprmasq {
    // Hooks the Hyprland function with this qualified name, for example "CXWM::activateSurface", or this C linkage
    // name, and returns a pointer to its original. Throws if the name matches no function or several.
    void* hook(void* handle, std::string_view qualifiedName, void* replacement);
    // Removes every hook, in reverse order of installation.
    void unhookAll();

    // Replaces a request handler of protocol devices, those of one client at a time, where Hyprland has no hook that
    // sees the request, and puts Hyprland's handlers back in restore(). Device is a device resource, wireOf gives its
    // wire resource and handlerOf the handler there, see PrivateAccess.hpp.
    template <typename Device, typename Wire, typename Handler>
    class CHandlerWraps {
      public:
        constexpr CHandlerWraps(SP<Wire>& (*wireOf)(Device&), Handler& (*handlerOf)(Wire&)) : m_wireOf(wireOf), m_handlerOf(handlerOf) {}

        // Replaces the handler of each of the client's devices that is not wrapped yet, also of ones created since the
        // last call, with make(original, device), unless its wire resource is gone.
        template <typename Make>
        void wrap(const std::vector<SP<Device>>& devices, wl_client* client, Make&& make) {
            std::erase_if(m_wrapped, [](const auto& w) { return !w.device; });
            for (const auto& device : devices) {
                if (!device || device->client() != client || std::ranges::any_of(m_wrapped, [&](const auto& w) { return w.device == device; }))
                    continue;
                auto& wire = m_wireOf(*device);
                if (!wire || !wire->resource())
                    continue;
                auto& handler = m_handlerOf(*wire);
                m_wrapped.push_back({.device = device, .original = handler});
                handler = make(handler, device);
            }
        }

        // Puts Hyprland's handlers back on the devices that are still there.
        void restore() {
            for (auto& w : m_wrapped) {
                if (auto device = w.device.lock(); device && m_wireOf(*device))
                    m_handlerOf(*m_wireOf(*device)) = std::move(w.original);
            }
            m_wrapped.clear();
        }

      private:
        struct SWrapped {
            WP<Device> device;
            Handler    original; // Hyprland's handler
        };
        SP<Wire>& (*m_wireOf)(Device&);
        Handler& (*m_handlerOf)(Wire&);
        std::vector<SWrapped> m_wrapped;
    };
}
