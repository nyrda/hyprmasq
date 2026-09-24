#include "Commands.hpp"

#include <hyprland/src/plugins/PluginAPI.hpp>

static HANDLE PHANDLE = nullptr;

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    PHANDLE = handle;

    HyprlandAPI::registerHyprCtlCommand(PHANDLE, SHyprCtlCommand{.name = "hyprmasq", .exact = false, .fn = hyprmasq::handleCommand});

    return {"hyprmasq", "Per-window screenshots for Hyprland", "daniel", "0.2.0"};
}

APICALL EXPORT void PLUGIN_EXIT() {
    PHANDLE = nullptr;
}
