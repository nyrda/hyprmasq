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

#include <cstring>
#include <format>
#include <stdexcept>

static HANDLE PHANDLE = nullptr;

// Releases what init installed, in the order that Wait.hpp and Selection.hpp require, also after a partial init: every
// shutdown is safe without its init.
static void shutdownAll() {
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
}

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    PHANDLE = handle;
    // Hyprland's loader compares only the API version, and the plugin reads private members of Hyprland's classes, whose
    // layout another build can change: a plugin built for another build could crash Hyprland.
    const char* running = __hyprland_api_get_hash();
    const char* built   = __hyprland_api_get_client_hash();
    if (std::strcmp(running, built) != 0) {
        HyprlandAPI::addNotification(handle, "[hyprmasq] Not loaded: it was built for another Hyprland build. Rebuild it against the running Hyprland.",
                                     CHyprColor{1.0, 0.2, 0.2, 1.0}, 10000);
        throw std::runtime_error(std::format("hyprmasq was built for Hyprland {}, not the running {}", built, running));
    }
    try {
        hyprmasq::initEis(handle);
        hyprmasq::selection::init(handle);
        hyprmasq::x11::init(handle);
        hyprmasq::deferred::init(handle);
        hyprmasq::cursor::init(handle);
        hyprmasq::launch::init();
        hyprmasq::placement::init(handle);
    } catch (...) {
        shutdownAll();
        throw;
    }

    HyprlandAPI::registerHyprCtlCommand(PHANDLE, SHyprCtlCommand{.name = "hyprmasq", .exact = false, .fn = hyprmasq::handleCommand});

    return {"hyprmasq", "Per-window screenshots and EIS input for Hyprland", "nyrda", std::string{hyprmasq::VERSION}};
}

APICALL EXPORT void PLUGIN_EXIT() {
    shutdownAll();
    PHANDLE = nullptr;
}
