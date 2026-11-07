// A test-only plugin that calls two functions that hyprmasq hooks, as an optimizing build of Hyprland calls them, and
// says whether the argument it passed by value came back as it went in:
//
//   hyprctl hmprobe grab <window address>   CXDGShellProtocol::addOrStartGrab with the window's first xdg popup
//   hyprctl hmprobe focus                   CSeatManager::setKeyboardFocus with the surface that has the keyboard focus
//
// It answers "kept" when the argument still holds the object and the object has as many strong references as before
// the call, "moved" when the call took the object out of the argument, "references B -> A" when the call kept B
// references fewer or more, or "error: ..." when there is nothing to pass.
//
// A class argument by value, such as an SP, is an object that the caller makes, passes by its address and destroys
// after the call. A build that knows the function, as Arch's Hyprland with LTO knows addOrStartGrab, may release the
// argument's reference through the pointer it held before the call, so a hook that moved out of the argument released
// that reference a second time. This plugin makes the argument itself and calls the function by its address, which
// is how the C++ ABI passes such an argument, so it sees what the hook left in it, whichever compiler built Hyprland.
// The grab is for a popup of a hidden window that hyprmasq drives, whose grab its hook refuses, and the focus is the
// one that the seat has, which Hyprland keeps: neither call changes Hyprland's state. nix/call-probe.nix builds it,
// and nothing installs it.

#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/desktop/view/Popup.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/managers/SeatManager.hpp>
#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/protocols/XDGShell.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>

#include <dlfcn.h>

#include <format>
#include <string>

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

namespace {
    // The function's address in Hyprland's executable, where a hook of it jumps to the plugin that hooks it.
    void* function(const char* symbol) {
        void* program = dlopen(nullptr, RTLD_LAZY);
        void* address = program ? dlsym(program, symbol) : nullptr;
        if (program)
            dlclose(program);
        return address;
    }

    // Calls the member function at address on self with argument passed as the ABI passes a class by value, and tells
    // what is left in the argument.
    template <typename T>
    std::string call(void* address, void* self, const SP<T>& object) {
        if (!address)
            return "error: no such function";
        SP<T>      argument = object;
        const auto before   = argument.strongRef();
        reinterpret_cast<void (*)(void*, SP<T>*)>(address)(self, &argument);
        if (argument.get() != object.get())
            return "moved";
        if (const auto after = argument.strongRef(); after != before)
            return std::format("references {} -> {}", before, after);
        return "kept";
    }

    SP<CXDGPopupResource> firstPopup(const std::string& address) {
        for (const auto& window : Desktop::windowState()->windows()) {
            if (!window || std::format("0x{:x}", rc<uintptr_t>(window.get())) != address || !window->m_popupHead)
                continue;
            SP<CXDGPopupResource> found;
            window->m_popupHead->breadthfirst(
                [&](SP<Desktop::View::CPopup> popup, void*) {
                    const auto surface = popup->resource();
                    if (found || !surface || !surface->m_role || surface->m_role->role() != SURFACE_ROLE_XDG_SHELL)
                        return;
                    const auto xdg = dc<CXDGSurfaceRole*>(surface->m_role.get())->m_xdgSurface.lock();
                    found          = xdg ? xdg->m_popup.lock() : nullptr;
                },
                nullptr);
            return found;
        }
        return nullptr;
    }

    std::string probe(eHyprCtlOutputFormat, std::string request) {
        if (request.starts_with("hmprobe grab ")) {
            const auto popup = firstPopup(request.substr(13));
            if (!popup)
                return "error: the window has no xdg popup";
            return call(function("_ZN17CXDGShellProtocol14addOrStartGrabEN9Hyprutils6Memory14CSharedPointerI17CXDGPopupResourceEE"), PROTO::xdgShell.get(), popup);
        }
        if (request == "hmprobe focus") {
            const auto surface = g_pSeatManager->m_state.keyboardFocus.lock();
            if (!surface)
                return "error: no keyboard focus";
            return call(function("_ZN12CSeatManager16setKeyboardFocusEN9Hyprutils6Memory14CSharedPointerI18CWLSurfaceResourceEE"), g_pSeatManager.get(), surface);
        }
        return "usage: hmprobe grab <window address> | hmprobe focus";
    }
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    HyprlandAPI::registerHyprCtlCommand(handle, SHyprCtlCommand{.name = "hmprobe", .exact = false, .fn = probe});
    return {"hyprmasq-call-probe", "Calls hooked functions as an optimizing build of Hyprland does", "hyprmasq", "1"};
}

APICALL EXPORT void PLUGIN_EXIT() {}
