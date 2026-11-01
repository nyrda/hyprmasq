#pragma once
#include <hyprland/src/helpers/memory/Memory.hpp>

#include <algorithm>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

struct wl_client;

namespace hyprmasq {
    // The type of a hook of the function that F points to, and of its original: the function's parameters, with the
    // object first for a member function.
    template <typename F>
    struct SHookOf;
    template <typename R, typename C, typename... A>
    struct SHookOf<R (C::*)(A...)> {
        using type = R (*)(C*, A...);
    };
    template <typename R, typename... A>
    struct SHookOf<R (*)(A...)> {
        using type = R (*)(A...);
    };
    template <typename F>
    using HookFn = typename SHookOf<F>::type;

    // Compiles only while Hyprland declares Function with type F. A hook and its original are called through a function
    // type of the plugin's own, which nothing else checks against Hyprland's, so every hooked function has an explicit
    // instantiation, `template struct hyprmasq::SPinned<F, &C::f>;`, beside its hook(): the hook's parameters follow F,
    // and F those of the pinned Hyprland's headers. An explicit instantiation may name private members, see
    // PrivateAccess.cpp.
    template <typename F, F Function>
    struct SPinned {};

    // Hooks the Hyprland function with this symbol, for example "_ZN4CXWM13handleFocusInEP20xcb_focus_in_event_t", and
    // returns a pointer to its original. Signature is what the symbol demangles to, here
    // "CXWM::handleFocusIn(xcb_focus_in_event_t*)", which a function with C linkage has as its symbol too, so that a
    // symbol cannot name another function than its signature says. Throws if Hyprland has no function of that symbol.
    void* hook(void* handle, const char* symbol, std::string_view signature, void* replacement);
    // The same for a function whose pointer has type F, with a replacement of the matching type, see SPinned.
    template <typename F>
    HookFn<F> hook(void* handle, const char* symbol, std::string_view signature, std::type_identity_t<HookFn<F>> replacement) {
        return reinterpret_cast<HookFn<F>>(hook(handle, symbol, signature, reinterpret_cast<void*>(replacement)));
    }
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
