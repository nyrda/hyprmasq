#include "Commands.hpp"
#include "Eis.hpp"
#include "Hooks.hpp"
#include "X11.hpp"

#include <hyprland/src/plugins/PluginAPI.hpp>

static HANDLE  PHANDLE = nullptr;

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    PHANDLE = handle;
    try {
        hyprmasq::initEis(handle);
        hyprmasq::x11::init(handle);
    } catch (...) {
        hyprmasq::x11::shutdown();
        hyprmasq::unhookAll();
        throw;
    }

    HyprlandAPI::registerHyprCtlCommand(PHANDLE, SHyprCtlCommand{.name = "hyprmasq", .exact = false, .fn = hyprmasq::handleCommand});

    return {"hyprmasq", "Per-window screenshots and EIS input for Hyprland", "nyrda", "0.2.0"};
}

APICALL EXPORT void PLUGIN_EXIT() {
    hyprmasq::shutdownEis();
    hyprmasq::x11::shutdown();
    hyprmasq::unhookAll();
    PHANDLE = nullptr;
}
