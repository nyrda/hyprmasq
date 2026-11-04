#pragma once
#include "Error.hpp"

#include <hyprland/src/helpers/memory/Memory.hpp>

#include <algorithm>
#include <cstdint>
#include <expected>
#include <optional>
#include <string>
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
    // instantiation, `template struct hyprmasq::SPinned<F, &C::f>;`, beside its defineHook(): the hook's parameters
    // follow F, and F those of the pinned Hyprland's headers. An explicit instantiation may name private members, see
    // PrivateAccess.cpp.
    template <typename F, F Function>
    struct SPinned {};

    // Hyprland hooks a function for one plugin at a time, and refuses a second hook of it. So the plugin resolves every
    // function it hooks at load, and installs the hooks in groups, one for each feature, most only while their feature is
    // in use: a feature whose group another plugin's hook refuses is refused, with the function named, and the rest of the
    // plugin goes on. A group is installed all or nothing. Groups that share a hook install it once, and it goes with the
    // last of them. Removing a hook frees the code that calls through it run in, so while a hook of the plugin runs, a
    // removal waits for the end of the event loop's iteration, and an install before then keeps the hook. See "Other
    // plugins" in docs/automation.md. Main thread only.
    class CHook;
    class CHookGroup;

    namespace detail {
        // The hooks of the plugin that are running, one inside another.
        extern int hookDepth;
        struct SHookCall {
            SHookCall() {
                ++hookDepth;
            }
            ~SHookCall() {
                --hookDepth;
            }
            SHookCall(const SHookCall&)            = delete;
            SHookCall& operator=(const SHookCall&) = delete;
        };
        // What Hyprland calls instead of the function: Replacement, counted as a running hook.
        template <auto Replacement>
        struct SCounted;
        template <typename R, typename... A, R (*Replacement)(A...)>
        struct SCounted<Replacement> {
            static R call(A... args) {
                SHookCall running;
                return Replacement(std::forward<A>(args)...);
            }
        };
        CHook& defineHook(void* handle, const char* symbol, std::string_view signature, void* replacement, void** original);
    }

    // Resolves the Hyprland function with this symbol, for example "_ZN4CXWM13handleFocusInEP20xcb_focus_in_event_t",
    // and checks it, without hooking it. Signature is what the symbol demangles to, here
    // "CXWM::handleFocusIn(xcb_focus_in_event_t*)", which a function with C linkage has as its symbol too, so that a
    // symbol cannot name another function than its signature says. Throws if Hyprland has no function of that symbol.
    // While a group with the hook is installed, Replacement takes the function's calls, and original points to the
    // function's original, which only Replacement calls. F is the function's pointer type, see SPinned.
    template <typename F, auto Replacement>
    CHook& defineHook(void* handle, const char* symbol, std::string_view signature, HookFn<F>& original) {
        static_assert(std::is_same_v<decltype(Replacement), HookFn<F>>, "a hook takes the parameters of its function");
        return detail::defineHook(handle, symbol, signature, reinterpret_cast<void*>(&detail::SCounted<Replacement>::call), reinterpret_cast<void**>(&original));
    }

    // Keeps a group installed while it lives.
    class CHookClaim {
      public:
        CHookClaim(CHookClaim&& other) noexcept : m_group(std::exchange(other.m_group, nullptr)) {}
        CHookClaim& operator=(CHookClaim&& other) noexcept;
        ~CHookClaim();
        CHookClaim(const CHookClaim&)            = delete;
        CHookClaim& operator=(const CHookClaim&) = delete;

      private:
        friend class CHookGroup;
        explicit CHookClaim(CHookGroup* group) : m_group(group) {}
        CHookGroup* m_group = nullptr;
    };

    class CHookGroup {
      public:
        // The name that `hooks` lists it by, and what it is needed for, as "input to windows".
        CHookGroup(std::string_view name, std::string_view feature, bool onDemand);
        CHookGroup(const CHookGroup&)            = delete;
        CHookGroup& operator=(const CHookGroup&) = delete;

        void add(CHook& hook);
        // Installs the hooks of the group that are not installed, all or none, unless the plugin has unloaded. The error
        // names a function: code hook_held while another plugin hooks it, internal if Hyprland cannot hook it.
        std::expected<CHookClaim, SError> claim();

        // For `hooks`.
        std::string_view name() const {
            return m_name;
        }
        bool onDemand() const {
            return m_onDemand;
        }
        bool installed() const {
            return m_claims > 0;
        }
        // The claims that keep it installed: one for each socket in use, for example.
        int claims() const {
            return m_claims;
        }
        // How often it went in since the load.
        uint64_t installs() const {
            return m_installs;
        }
        std::vector<std::string_view> functions() const;
        // Why the last claim was refused, if none succeeded since.
        const std::optional<SError>& refused() const {
            return m_refused;
        }

      private:
        friend class CHookClaim;
        friend void unhookAll();
        void release();

        std::string           m_name, m_feature;
        bool                  m_onDemand = true;
        std::vector<CHook*>   m_hooks;
        int                   m_claims   = 0;
        uint64_t              m_installs = 0;
        std::optional<SError> m_refused;
    };

    namespace hooks {
        // The groups, in the order `hooks` lists them. On demand: input to windows, also to X11 windows, keeping the
        // windows that the plugin placed from taking the focus through activation, keeping X selections that the agent
        // made from Wayland, ending an app's own copy that is the agent clipboard before the user's touch reaches the app,
        // and keeping windows from being told that they are suspended. For the plugin's lifetime:
        // placing the windows that emulated input opens, keeping idle timers from restarting on maps, and hyprctl calls
        // from other plugins.
        extern CHookGroup input, x11Input, launch, x11Selection, clipboard, suspension, placement, idle, pluginCalls;
        const std::vector<CHookGroup*>& all();
        // Installs a group for the plugin's lifetime. A refusal disables the group's feature: it is logged, and shown as
        // a notification and by `hooks`.
        std::optional<CHookClaim> forLifetime(CHookGroup& group);
    }

    // Removes every hook now, also those whose removal waits, and refuses claims from then on. Claims that end later
    // change nothing.
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
