#include "Commands.hpp"
#include "Cursor.hpp"
#include "Deferred.hpp"
#include "Eis.hpp"
#include "Hooks.hpp"
#include "Launch.hpp"
#include "Placement.hpp"
#include "Selection.hpp"
#include "Wait.hpp"
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
        hyprmasq::selection::init(handle);
        hyprmasq::x11::init(handle);
        hyprmasq::deferred::init(handle);
        hyprmasq::cursor::init(handle);
        hyprmasq::launch::init();
        hyprmasq::placement::init(handle);
    } catch (...) {
        hyprmasq::placement::shutdown();
        hyprmasq::launch::shutdown();
        hyprmasq::wait::shutdown();
        hyprmasq::shutdownEis();
        hyprmasq::selection::shutdown();
        hyprmasq::cursor::shutdown();
        hyprmasq::deferred::shutdown();
        hyprmasq::x11::shutdown();
        hyprmasq::unhookAll();
        throw;
    }

    HyprlandAPI::registerHyprCtlCommand(PHANDLE, SHyprCtlCommand{.name = "hyprmasq", .exact = false, .fn = hyprmasq::handleCommand});

    return {"hyprmasq", "Per-window screenshots and EIS input for Hyprland", "nyrda", std::string{hyprmasq::VERSION}};
}

APICALL EXPORT void PLUGIN_EXIT() {
    hyprmasq::placement::shutdown();
    hyprmasq::launch::shutdown();
    // Pending waits hold threads that deferred::shutdown() joins.
    hyprmasq::wait::shutdown();
    hyprmasq::deferred::shutdown();
    hyprmasq::shutdownEis();
    hyprmasq::selection::shutdown();
    hyprmasq::cursor::shutdown();
    hyprmasq::x11::shutdown();
    hyprmasq::unhookAll();
    PHANDLE = nullptr;
}
