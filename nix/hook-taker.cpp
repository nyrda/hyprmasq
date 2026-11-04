// A test-only plugin that hooks one function that hyprmasq hooks too, as another plugin would, so that the VM test sees
// hyprmasq share Hyprland with it. Hyprland hooks a function for one plugin at a time, so this one does not load while
// hyprmasq holds the function, and hyprmasq refuses what needs the function while this one holds it. Its hook passes
// every call through, and goes when it unloads. nix/hook-taker.nix builds it once for each function, and nothing
// installs it:
//
//   hyprmasq-hook-taker-seat.so  CSeatManager::setKeyboardFocus, of the hooks of input to windows
//   hyprmasq-hook-taker-xwm.so   CXWM::activateSurface, of the hooks of input to X11 windows
//   hyprmasq-hook-taker-idle.so  CIdleNotifyProtocol::setInhibit, which hyprmasq hooks from its load

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

#include <cstdlib>
#include <stdexcept>
#include <string>
#include <string_view>

namespace {
    // Compiles only while Hyprland declares the function with type F. An explicit instantiation may name a private
    // member, as CXWM::activateSurface is.
    template <typename F, F Function>
    struct SPinned {};

    HANDLE         handle = nullptr;
    CFunctionHook* held   = nullptr;

#if defined(HOOK_TAKER_SEAT)
    constexpr const char*      NAME      = "hyprmasq-hook-taker-seat";
    constexpr const char*      SYMBOL    = "_ZN12CSeatManager16setKeyboardFocusEN9Hyprutils6Memory14CSharedPointerI18CWLSurfaceResourceEE";
    constexpr std::string_view SIGNATURE = "CSeatManager::setKeyboardFocus(Hyprutils::Memory::CSharedPointer<CWLSurfaceResource>)";
    using Function                       = void (CSeatManager::*)(SP<CWLSurfaceResource>);
    template struct SPinned<Function, &CSeatManager::setKeyboardFocus>;
    using Original = void (*)(CSeatManager*, SP<CWLSurfaceResource>);
    void passThrough(CSeatManager* self, SP<CWLSurfaceResource> surface) {
        reinterpret_cast<Original>(held->m_original)(self, std::move(surface));
    }
#elif defined(HOOK_TAKER_XWM)
    constexpr const char*      NAME      = "hyprmasq-hook-taker-xwm";
    constexpr const char*      SYMBOL    = "_ZN4CXWM15activateSurfaceEN9Hyprutils6Memory14CSharedPointerI16CXWaylandSurfaceEEb";
    constexpr std::string_view SIGNATURE = "CXWM::activateSurface(Hyprutils::Memory::CSharedPointer<CXWaylandSurface>, bool)";
    using Function                       = void (CXWM::*)(SP<CXWaylandSurface>, bool);
    template struct SPinned<Function, &CXWM::activateSurface>;
    using Original = void (*)(CXWM*, SP<CXWaylandSurface>, bool);
    void passThrough(CXWM* self, SP<CXWaylandSurface> surface, bool activate) {
        reinterpret_cast<Original>(held->m_original)(self, std::move(surface), activate);
    }
#else
    constexpr const char*      NAME      = "hyprmasq-hook-taker-idle";
    constexpr const char*      SYMBOL    = "_ZN19CIdleNotifyProtocol10setInhibitEb";
    constexpr std::string_view SIGNATURE = "CIdleNotifyProtocol::setInhibit(bool)";
    using Function                       = void (CIdleNotifyProtocol::*)(bool);
    template struct SPinned<Function, &CIdleNotifyProtocol::setInhibit>;
    using Original = void (*)(CIdleNotifyProtocol*, bool);
    void passThrough(CIdleNotifyProtocol* self, bool inhibited) {
        reinterpret_cast<Original>(held->m_original)(self, inhibited);
    }
#endif
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
    void* address = program ? dlsym(program, SYMBOL) : nullptr;
    if (program)
        dlclose(program);
    if (!exact || !address)
        throw std::runtime_error(std::string{NAME} + ": Hyprland has no " + std::string{SIGNATURE});
    held = HyprlandAPI::createFunctionHook(handle, address, reinterpret_cast<const void*>(&passThrough));
    if (!held || !held->hook()) {
        if (held)
            HyprlandAPI::removeFunctionHook(handle, held);
        held = nullptr;
        throw std::runtime_error(std::string{NAME} + ": cannot hook " + std::string{SIGNATURE} + ", which another plugin may hold");
    }
    Log::logger->log(Log::INFO, NAME, "holds {}", SIGNATURE);
    return {NAME, "Test-only: holds a function that hyprmasq hooks", "hyprmasq", "1"};
}

APICALL EXPORT void PLUGIN_EXIT() {
    if (held)
        HyprlandAPI::removeFunctionHook(handle, held);
    held = nullptr;
}
