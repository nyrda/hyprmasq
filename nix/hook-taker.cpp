// A test-only plugin that hooks one function that hyprmasq hooks too, as another plugin would, so that the VM test sees
// hyprmasq share Hyprland with it. Its hook counts its calls and passes every one through, and goes when the plugin
// unloads. nix/hook-taker.nix builds it once for each function, and nothing installs it:
//
//   hyprmasq-hook-taker-seat.so  CSeatManager::setKeyboardFocus, of the hooks of input to windows
//   hyprmasq-hook-taker-xwm.so   CXWM::activateSurface, of the hooks of input to X11 windows
//   hyprmasq-hook-taker-idle.so  CIdleNotifyProtocol::setInhibit, which hyprmasq hooks from its load
//
// Those hook as a plugin does that knows nothing of shared hooks: with CFunctionHook::hook(), which Hyprland refuses
// while hyprmasq holds the function, and which hyprmasq's hook joins. Such a plugin removes its hook with unhook(),
// which takes hyprmasq's hook of the function out with it. Each is also built in two more ways:
//
//   -opaque.so  the same, but then it replaces the code that hook() wrote for its hook with other code that does the
//               same. No hook can join such a hook, so hyprmasq refuses what needs the function.
//   -chain.so   with src/HookChain.cpp, as a plugin that shares hooks does: it joins hyprmasq's hook of the function,
//               hyprmasq's joins its hook, and either leaves without taking the other out.
//
//   hyprctl hmtaker   "calls N" for the first two kinds, and for the third, as `hyprctl hmchain`, also "linked yes|no"
//                     and "members N": whether calls reach its hook, and how many hooks they run through

#if defined(HOOK_TAKER_CHAIN)
#include "HookChain.hpp"
#endif

#include <hyprland/src/debug/log/Logger.hpp>
#include <hyprland/src/plugins/HookSystem.hpp>
#include <hyprland/src/plugins/PluginAPI.hpp>

#if defined(HOOK_TAKER_SEAT)
#include <hyprland/src/managers/SeatManager.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#elif defined(HOOK_TAKER_XWM)
#include <hyprland/src/xwayland/XSurface.hpp>
#include <hyprland/src/xwayland/XWM.hpp>
#elif defined(HOOK_TAKER_IDLE)
#include <hyprland/src/protocols/IdleNotify.hpp>
#else
#error "build with one of HOOK_TAKER_SEAT, HOOK_TAKER_XWM and HOOK_TAKER_IDLE"
#endif

#include <cxxabi.h>
#include <dlfcn.h>

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <format>
#include <stdexcept>
#include <string>
#include <string_view>

namespace {
    // Compiles only while Hyprland declares the function with type F. An explicit instantiation may name a private
    // member, as CXWM::activateSurface is.
    template <typename F, F Function>
    struct SPinned {};

    HANDLE         handle  = nullptr;
    CFunctionHook* held    = nullptr;
    void*          address = nullptr;
    uint64_t       calls   = 0;

#if defined(HOOK_TAKER_CHAIN)
#define HOOK_TAKER_SUFFIX "-chain"
    constexpr const char* COMMAND = "hmchain";
#elif defined(HOOK_TAKER_OPAQUE)
#define HOOK_TAKER_SUFFIX "-opaque"
    constexpr const char* COMMAND = "hmtaker";
#else
#define HOOK_TAKER_SUFFIX ""
    constexpr const char* COMMAND = "hmtaker";
#endif

#if defined(HOOK_TAKER_SEAT)
    constexpr const char*      NAME      = "hyprmasq-hook-taker-seat" HOOK_TAKER_SUFFIX;
    constexpr const char*      SYMBOL    = "_ZN12CSeatManager16setKeyboardFocusEN9Hyprutils6Memory14CSharedPointerI18CWLSurfaceResourceEE";
    constexpr std::string_view SIGNATURE = "CSeatManager::setKeyboardFocus(Hyprutils::Memory::CSharedPointer<CWLSurfaceResource>)";
    using Function                       = void (CSeatManager::*)(SP<CWLSurfaceResource>);
    template struct SPinned<Function, &CSeatManager::setKeyboardFocus>;
    using Original = void (*)(CSeatManager*, SP<CWLSurfaceResource>);
    void passThrough(CSeatManager* self, SP<CWLSurfaceResource> surface) {
        ++calls;
        reinterpret_cast<Original>(held->m_original)(self, surface);
    }
#elif defined(HOOK_TAKER_XWM)
    constexpr const char*      NAME      = "hyprmasq-hook-taker-xwm" HOOK_TAKER_SUFFIX;
    constexpr const char*      SYMBOL    = "_ZN4CXWM15activateSurfaceEN9Hyprutils6Memory14CSharedPointerI16CXWaylandSurfaceEEb";
    constexpr std::string_view SIGNATURE = "CXWM::activateSurface(Hyprutils::Memory::CSharedPointer<CXWaylandSurface>, bool)";
    using Function                       = void (CXWM::*)(SP<CXWaylandSurface>, bool);
    template struct SPinned<Function, &CXWM::activateSurface>;
    using Original = void (*)(CXWM*, SP<CXWaylandSurface>, bool);
    void passThrough(CXWM* self, SP<CXWaylandSurface> surface, bool activate) {
        ++calls;
        reinterpret_cast<Original>(held->m_original)(self, surface, activate);
    }
#else
    constexpr const char*      NAME      = "hyprmasq-hook-taker-idle" HOOK_TAKER_SUFFIX;
    constexpr const char*      SYMBOL    = "_ZN19CIdleNotifyProtocol10setInhibitEb";
    constexpr std::string_view SIGNATURE = "CIdleNotifyProtocol::setInhibit(bool)";
    using Function                       = void (CIdleNotifyProtocol::*)(bool);
    template struct SPinned<Function, &CIdleNotifyProtocol::setInhibit>;
    using Original = void (*)(CIdleNotifyProtocol*, bool);
    void passThrough(CIdleNotifyProtocol* self, bool inhibited) {
        ++calls;
        reinterpret_cast<Original>(held->m_original)(self, inhibited);
    }
#endif

#if __has_include(<hyprland/src/ipc/s1/S1.hpp>)
    IPC::Socket1::SResponse report(const IPC::Socket1::SRequest&) {
#else
    std::string report(eHyprCtlOutputFormat, std::string) {
#endif
#if defined(HOOK_TAKER_CHAIN)
        return std::format("calls {} linked {} members {}", calls, held && hookchain::linked(*held) ? "yes" : "no", hookchain::members(address));
#else
        return std::format("calls {}", calls);
#endif
    }

#if defined(HOOK_TAKER_OPAQUE)
    // Replaces `movabs rax, destination; jmp rax`, which hook() wrote where the function's first bytes now jump, with
    // `movabs r11, destination; jmp r11`.
    void makeOpaque() {
        auto*   code   = static_cast<unsigned char*>(address);
        int32_t offset = 0;
        std::memcpy(&offset, code + 1, sizeof(offset));
        unsigned char* slot      = code + 5 + offset;
        unsigned char  other[13] = {0x49, 0xBB, 0, 0, 0, 0, 0, 0, 0, 0, 0x41, 0xFF, 0xE3};
        std::memcpy(other + 2, slot + 2, 8);
        std::memcpy(slot, other, sizeof(other));
    }
#endif

    // Installs the hook: as the function's first, or, for the kind that shares hooks, with those that are there.
    bool install() {
#if defined(HOOK_TAKER_CHAIN)
        if (hookchain::hooked(address))
            return !hookchain::join(*held);
#endif
        if (!held->hook())
            return false;
#if defined(HOOK_TAKER_OPAQUE)
        makeOpaque();
#endif
        return true;
    }
}

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE h) {
    if (std::string_view{__hyprland_api_get_hash()} != __hyprland_api_get_client_hash())
        throw std::runtime_error(std::string{NAME} + ": built for another Hyprland build");
    handle               = h;
    int        status    = 0;
    char*      demangled = abi::__cxa_demangle(SYMBOL, nullptr, nullptr, &status);
    const bool exact     = status == 0 && demangled && SIGNATURE == demangled;
    std::free(demangled);
    void* program = dlopen(nullptr, RTLD_LAZY);
    address       = program ? dlsym(program, SYMBOL) : nullptr;
    if (program)
        dlclose(program);
    if (!exact || !address)
        throw std::runtime_error(std::string{NAME} + ": Hyprland has no " + std::string{SIGNATURE});
    held = HyprlandAPI::createFunctionHook(handle, address, reinterpret_cast<const void*>(&passThrough));
    if (!held || !install()) {
        if (held)
            HyprlandAPI::removeFunctionHook(handle, held);
        held = nullptr;
        throw std::runtime_error(std::string{NAME} + ": cannot hook " + std::string{SIGNATURE} + ", which another plugin may hold");
    }
#if __has_include(<hyprland/src/ipc/s1/S1.hpp>)
    HyprlandAPI::registerHyprCtlCommand(handle, IPC::Socket1::SCommand{.name = COMMAND, .match = IPC::Socket1::COMMAND_MATCH_PREFIX, .handler = report});
#else
    HyprlandAPI::registerHyprCtlCommand(handle, SHyprCtlCommand{.name = COMMAND, .exact = false, .fn = report});
#endif
    Log::logger->log(Log::INFO, NAME, "holds {}", SIGNATURE);
    return {NAME, "Test-only: holds a function that hyprmasq hooks", "hyprmasq", "1"};
}

APICALL EXPORT void PLUGIN_EXIT() {
    if (held) {
#if defined(HOOK_TAKER_CHAIN)
        hookchain::leave(*held);
#endif
        // For the other kinds this is unhook(), which puts the function's first bytes back.
        HyprlandAPI::removeFunctionHook(handle, held);
    }
    held = nullptr;
#if defined(HOOK_TAKER_CHAIN)
    hookchain::shutdown();
#endif
}
