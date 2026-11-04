#include "Hooks.hpp"
#include "PrivateAccess.hpp"

#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/debug/log/Logger.hpp>
#include <hyprland/src/plugins/HookSystem.hpp>
#include <hyprland/src/plugins/PluginAPI.hpp>

#include <cxxabi.h>
#include <dlfcn.h>
#include <sys/mman.h>
#include <unistd.h>
#include <array>
#include <cstdlib>
#include <cstring>
#include <format>
#include <memory>
#include <ranges>
#include <stdexcept>
#include <string>
#include <vector>

namespace hyprmasq {
    // One Hyprland function that the plugin hooks, resolved at load. Hyprland's CFunctionHook::hook() takes 64 bytes for
    // the trampolines of a hook each time, which Hyprland never frees, not even when the hook is removed, and a Hyprland
    // session runs for weeks. So the plugin hooks each function with hook() once, keeps that CFunctionHook until it
    // unloads, removes the hook with unhook(), and installs it again by putting back the jump that hook() wrote, to the
    // same trampolines, which nothing else writes or frees: PrivateAccess.cpp pins the layout of Hyprland's hook system, and
    // rehook() checks the trampolines' code before it jumps to it.
    class CHook {
      public:
        std::string    signature;
        std::string    name; // the function's qualified name, without its parameters
        void*          address     = nullptr;
        void*          replacement = nullptr;
        void**         original    = nullptr;
        CFunctionHook* function    = nullptr; // from the first install until unhookAll
        bool           installed   = false;
        // Why it is not installed again: hook() failed, or its trampolines are no longer the plugin's.
        std::optional<std::string> broken;
        int                        claims   = 0;     // installed groups that have it
        bool                       removing = false; // its removal waits for the end of the event loop's iteration
        // What the first hook() set up: its trampolines, the function's first bytes, and the jump that replaced them.
        void*                      launch = nullptr;
        void*                      land   = nullptr;
        std::vector<unsigned char> replaced, jump;
        // The trampolines' slots as the first hook() left them: the jump to the replacement, and the original's first
        // instructions with the jump back, each at the start of the HOOK_TRAMPOLINE_MAX_SIZE bytes that Hyprland
        // reserved for it, of which another hook given the slot would change some.
        std::array<unsigned char, HOOK_TRAMPOLINE_MAX_SIZE> launchCode{}, landCode{};
    };

    namespace detail {
        int hookDepth = 0;
    }

    namespace {
        void*                               pluginHandle = nullptr;
        bool                                loaded       = false; // from the first defineHook until unhookAll
        std::vector<std::unique_ptr<CHook>> defined;
        wl_event_source*                    removals = nullptr;

        // What a symbol demangles to. A name with C linkage stays as it is.
        std::string demangle(const char* symbol) {
            int         status = 0;
            char*       name   = abi::__cxa_demangle(symbol, nullptr, nullptr, &status);
            std::string result = status == 0 && name ? name : symbol;
            std::free(name);
            return result;
        }

        void removeNow(CHook& hook) {
            hook.removing = false;
            if (!hook.installed)
                return;
            // Writes the function's first bytes back and takes it out of m_activeHooks, and leaves the trampolines.
            hook.function->unhook();
            hook.installed = false;
            *hook.original = nullptr;
        }

        // Writes code over the function's first bytes, as hook() and unhook() do.
        bool writeCode(void* at, const std::vector<unsigned char>& bytes) {
            const auto page   = sc<uintptr_t>(sysconf(_SC_PAGE_SIZE));
            const auto first  = rc<uintptr_t>(at) / page * page;
            const auto length = (rc<uintptr_t>(at) + bytes.size() - first + page - 1) / page * page;
            if (mprotect(rc<void*>(first), length, PROT_READ | PROT_WRITE | PROT_EXEC) != 0)
                return false;
            std::memcpy(at, bytes.data(), bytes.size());
            mprotect(rc<void*>(first), length, PROT_READ | PROT_EXEC);
            return true;
        }

        // Puts the hook back as hook() left it the first time: the jump in place of the function's first bytes, and the
        // state of the CFunctionHook and of m_activeHooks, so that other plugins see the function hooked, and Hyprland's
        // unhook() removes it, also at the unload. Or why not.
        std::optional<std::string> rehook(CHook& hook) {
            auto&      function = *hook.function;
            const auto size     = hook.replaced.size();
            // A Hyprland that reused the trampolines for another hook would run that hook's code instead. The jump never
            // goes in then, and neither does a hook() of the function, which would hide it.
            if (std::memcmp(hook.launch, hook.launchCode.data(), hook.launchCode.size()) != 0 || std::memcmp(hook.land, hook.landCode.data(), hook.landCode.size()) != 0) {
                Log::logger->log(Log::ERR,
                                 "[hyprmasq] the trampolines of hyprmasq's hook of {} changed since its first install, so this Hyprland reuses them: not installing it again",
                                 hook.name);
                hook.broken = std::format("Hyprland reused the memory of hyprmasq's hook of {}", hook.name);
                return hook.broken;
            }
            // The function's first bytes are as hook() found them, which another plugin's hook would have replaced.
            if (activeOf(function) || size == 0 || std::memcmp(hook.address, hook.replaced.data(), size) != 0)
                return std::format("the code of {} changed since hyprmasq hooked it", hook.name);
            if (!writeCode(hook.address, hook.jump))
                return std::format("Hyprland cannot hook {}", hook.name);
            launchTrampolineOf(function) = hook.launch;
            landTrampolineOf(function)   = hook.land;
            hookLenOf(function)          = size;
            originalBytesOf(function)    = hook.replaced;
            activeOf(function)           = true;
            function.m_original          = hook.land;
            activeHooksOf(*g_pFunctionHookSystem).emplace(rc<uint64_t>(hook.address));
            return std::nullopt;
        }

        // Installs the hook, or tells why not.
        std::optional<std::string> install(CHook& hook) {
            if (hook.broken)
                return hook.broken;
            if (hook.function) {
                if (auto why = rehook(hook))
                    return why;
            } else {
                auto* created = HyprlandAPI::createFunctionHook(pluginHandle, hook.address, hook.replacement);
                if (!created)
                    return std::format("Hyprland cannot hook {}", hook.name);
                // A hook() that fails, for a function whose code it cannot move, takes trampolines too.
                if (!created->hook()) {
                    HyprlandAPI::removeFunctionHook(pluginHandle, created);
                    hook.broken = std::format("Hyprland cannot hook {}", hook.name);
                    return hook.broken;
                }
                hook.function = created;
                hook.launch   = launchTrampolineOf(*created);
                hook.land     = landTrampolineOf(*created);
                hook.replaced = originalBytesOf(*created);
                hook.jump.assign(sc<unsigned char*>(hook.address), sc<unsigned char*>(hook.address) + hookLenOf(*created));
                std::memcpy(hook.launchCode.data(), hook.launch, hook.launchCode.size());
                std::memcpy(hook.landCode.data(), hook.land, hook.landCode.size());
            }
            hook.installed = true;
            *hook.original = hook.function->m_original;
            return std::nullopt;
        }

        void scheduleRemovals();

        int flushRemovals(void*) {
            removals = nullptr; // libwayland removes it after this call
            // A hook that runs the event loop itself would still be on the stack.
            if (detail::hookDepth > 0) {
                scheduleRemovals();
                return 0;
            }
            for (auto& hook : defined)
                if (hook->removing && hook->claims == 0)
                    removeNow(*hook);
            return 0;
        }

        void scheduleRemovals() {
            if (!removals && g_pCompositor)
                removals = wl_event_loop_add_idle(g_pCompositor->m_wlEventLoop, [](void* data) { flushRemovals(data); }, nullptr);
        }

        // The hook's function runs through it now, as one of the plugin's hooks runs.
        void remove(CHook& hook) {
            if (detail::hookDepth == 0) {
                removeNow(hook);
                return;
            }
            hook.removing = true;
            scheduleRemovals();
            if (!removals)
                removeNow(hook);
        }
    }

    CHook& detail::defineHook(void* handle, const char* symbol, std::string_view signature, void* replacement, void** original) {
        if (const auto named = demangle(symbol); named != signature)
            throw std::runtime_error(std::format("hyprmasq: the hook of {} names {}", signature, named));
        // Hyprland's executable exports its functions. HyprlandAPI::findFunctionsByName looks them up the same way, after
        // it has run nm on the executable, which needs binutils and takes about 120 ms of the main thread. The main
        // program's own scope leaves out the plugin, which may have a copy of a function inlined from a header.
        void* program = dlopen(nullptr, RTLD_LAZY);
        void* address = program ? dlsym(program, symbol) : nullptr;
        if (program)
            dlclose(program);
        if (!address)
            throw std::runtime_error(std::format("hyprmasq: missing hook {}: the running Hyprland has no such function", signature));
        pluginHandle     = handle;
        loaded           = true;
        auto& hook       = *defined.emplace_back(std::make_unique<CHook>());
        hook.signature   = signature;
        hook.name        = std::string{signature.substr(0, signature.find('('))};
        hook.address     = address;
        hook.replacement = replacement;
        hook.original    = original;
        *original        = nullptr;
        return hook;
    }

    CHookClaim& CHookClaim::operator=(CHookClaim&& other) noexcept {
        if (this != &other) {
            if (m_group)
                m_group->release();
            m_group = std::exchange(other.m_group, nullptr);
        }
        return *this;
    }

    CHookClaim::~CHookClaim() {
        if (m_group)
            m_group->release();
    }

    CHookGroup::CHookGroup(std::string_view name, std::string_view feature, bool onDemand) : m_name(name), m_feature(feature), m_onDemand(onDemand) {}

    void CHookGroup::add(CHook& hook) {
        if (!std::ranges::contains(m_hooks, &hook))
            m_hooks.push_back(&hook);
    }

    std::vector<std::string_view> CHookGroup::functions() const {
        std::vector<std::string_view> result;
        for (const auto* hook : m_hooks)
            result.push_back(hook->signature);
        return result;
    }

    std::expected<CHookClaim, SError> CHookGroup::claim() {
        if (!loaded)
            return std::unexpected(SError{eErrorCode::UNLOADED, "hyprmasq is unloading"});
        if (m_claims > 0) {
            ++m_claims;
            return CHookClaim{this};
        }
        const auto refuse = [&](SError error) -> std::unexpected<SError> {
            if (!m_refused || m_refused->message != error.message)
                Log::logger->log(Log::WARN, "[hyprmasq] {}", error.message);
            m_refused = error;
            return std::unexpected(std::move(error));
        };
        // Hyprland's hook() would log an error for each hook that another plugin holds.
        auto& active = activeHooksOf(*g_pFunctionHookSystem);
        for (const auto* hook : m_hooks) {
            if (!hook->installed && active.contains(rc<uint64_t>(hook->address)))
                return refuse(SError{eErrorCode::HOOK_HELD, std::format("another plugin hooks {}, which hyprmasq needs for {}", hook->name, m_feature)});
        }
        std::vector<CHook*> added;
        for (auto* hook : m_hooks) {
            if (hook->installed)
                continue;
            if (const auto why = install(*hook)) {
                // None of them can have a call running through it yet.
                for (auto* undo : added)
                    removeNow(*undo);
                return refuse(SError{eErrorCode::INTERNAL, std::format("{}, which hyprmasq needs for {}", *why, m_feature)});
            }
            added.push_back(hook);
        }
        for (auto* hook : m_hooks) {
            ++hook->claims;
            hook->removing = false;
        }
        ++m_claims;
        ++m_installs;
        if (m_refused)
            Log::logger->log(Log::INFO, "[hyprmasq] the hooks for {} are installed again", m_feature);
        m_refused.reset();
        return CHookClaim{this};
    }

    void CHookGroup::release() {
        if (!loaded || m_claims == 0 || --m_claims > 0)
            return;
        for (auto* hook : m_hooks) {
            if (--hook->claims == 0)
                remove(*hook);
        }
    }

    namespace hooks {
        CHookGroup input{"input", "input to windows", true};
        CHookGroup x11Input{"x11-input", "input to X11 windows", true};
        CHookGroup launch{"launch", "keeping the windows that it places from taking the focus", true};
        CHookGroup x11Selection{"x11-selection", "keeping the agent's X selections from your Wayland selections", true};
        CHookGroup clipboard{"clipboard", "ending the agent's copy in its app before your touch reaches that app", true};
        CHookGroup suspension{"suspension", "keeping hidden windows that it drives or waits on from being told that they are suspended", true};
        CHookGroup placement{"placement", "placing the windows that emulated input opens", false};
        CHookGroup idle{"idle", "keeping windows that map and unmap from restarting idle timers", false};
        CHookGroup pluginCalls{"plugin-calls", "hyprctl calls from other plugins", false};

        const std::vector<CHookGroup*>& all() {
            static const std::vector<CHookGroup*> groups{&input, &x11Input, &launch, &x11Selection, &clipboard, &suspension, &placement, &idle, &pluginCalls};
            return groups;
        }

        std::optional<CHookClaim> forLifetime(CHookGroup& group) {
            auto claim = group.claim();
            if (claim)
                return std::move(*claim);
            HyprlandAPI::addNotification(pluginHandle, std::format("[hyprmasq] {}. That stays off until hyprmasq loads again.", claim.error().message),
                                         CHyprColor{1.0, 0.6, 0.2, 1.0}, 10000);
            return std::nullopt;
        }
    }

    void unhookAll() {
        if (removals)
            wl_event_source_remove(removals);
        removals = nullptr;
        for (auto& hook : std::views::reverse(defined)) {
            removeNow(*hook);
            if (hook->function)
                HyprlandAPI::removeFunctionHook(pluginHandle, hook->function);
            hook->function = nullptr;
            hook->claims   = 0;
        }
        // A library that the unload keeps mapped, see deferred::shutdown(), keeps these globals for its next load, which
        // defines every hook again.
        for (auto* group : hooks::all()) {
            group->m_hooks.clear();
            group->m_claims   = 0;
            group->m_installs = 0;
            group->m_refused.reset();
        }
        defined.clear();
        loaded = false;
    }
}
