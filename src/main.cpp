#include "Commands.hpp"
#include "Cursor.hpp"
#include "Deferred.hpp"
#include "Eis.hpp"
#include "Hooks.hpp"
#include "Idle.hpp"
#include "Launch.hpp"
#include "Placement.hpp"
#include "Screenshot.hpp"
#include "Selection.hpp"
#include "Suspension.hpp"
#include "Wait.hpp"
#include "X11.hpp"

#include <hyprland/src/plugins/PluginAPI.hpp>

#include <dlfcn.h>
#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <format>
#include <fstream>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

static HANDLE PHANDLE = nullptr;

namespace {
    // A package of prebuilt plugins installs hyprland-builds next to the plugin: the Hyprland builds that its CI verified
    // this binary for, as __hyprland_api_get_hash() names them, one per line, each followed by what it is. Builds whose
    // headers compile to this same binary run it as their own. Without the file, only the build it was compiled against
    // does.
    std::optional<std::vector<std::string>> verifiedBuilds() {
        Dl_info info{};
        if (!dladdr(reinterpret_cast<void*>(&verifiedBuilds), &info) || !info.dli_fname)
            return std::nullopt;
        std::ifstream file(std::filesystem::path(info.dli_fname).parent_path() / "hyprland-builds");
        if (!file)
            return std::nullopt;
        std::vector<std::string> builds;
        for (std::string line; std::getline(file, line);) {
            std::istringstream words(line);
            if (std::string build; words >> build && !build.starts_with("#"))
                builds.push_back(build);
        }
        return builds;
    }

    // Why the plugin did not load, for hyprmasq-mcp to report: next to Hyprland's socket, removed by a load that succeeds.
    std::optional<std::filesystem::path> refusalFile() {
        const char* runtime  = std::getenv("XDG_RUNTIME_DIR");
        const char* instance = std::getenv("HYPRLAND_INSTANCE_SIGNATURE");
        if (!runtime || !instance || !*runtime || !*instance)
            return std::nullopt;
        return std::filesystem::path(runtime) / "hypr" / instance / "hyprmasq-refused";
    }

    // Hyprland's loader compares only the API version, and the plugin reads private members of Hyprland's classes, whose
    // layout another build can change, see PrivateAccess.hpp: a plugin built for another build could crash Hyprland.
    void checkBuild(HANDLE handle) {
        const std::string running = __hyprland_api_get_hash();
        const std::string built   = __hyprland_api_get_client_hash();
        const auto        file    = refusalFile();
        const auto        builds  = verifiedBuilds();
        if (running == built || (builds && std::ranges::contains(*builds, running))) {
            if (file) {
                std::error_code ignored;
                std::filesystem::remove(*file, ignored);
            }
            return;
        }
        // The commit of the running Hyprland, which hyprctl version shows too.
        const auto commit = running.substr(0, std::min<size_t>(running.find('_'), 12));
        const auto why    = builds ? std::format("this hyprmasq package has no plugin for the running Hyprland build {} yet. Update it, as with yay -Syu, and restart "
                                                    "Hyprland.",
                                                 commit) :
                                     std::string{"it was built for another Hyprland build. Rebuild it against the running Hyprland."};
        if (file)
            std::ofstream(*file) << why << '\n';
        HyprlandAPI::addNotification(handle, "[hyprmasq] Not loaded: " + why, CHyprColor{1.0, 0.2, 0.2, 1.0}, 10000);
        throw std::runtime_error(std::format("hyprmasq was built for Hyprland {}, not the running {}", built, running));
    }
}

// Releases what init installed, in the order that Wait.hpp and Selection.hpp require, also after a partial init: every
// shutdown is safe without its init.
static void shutdownAll() {
    hyprmasq::placement::shutdown();
    hyprmasq::launch::shutdown();
    // Pending waits hold threads that deferred::shutdown() joins.
    hyprmasq::wait::shutdown();
    hyprmasq::shutdownCaptures();
    hyprmasq::deferred::shutdown();
    hyprmasq::shutdownEis();
    hyprmasq::selection::shutdown();
    hyprmasq::cursor::shutdown();
    hyprmasq::x11::shutdown();
    hyprmasq::idle::shutdown();
    hyprmasq::unhookAll();
}

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    PHANDLE = handle;
    checkBuild(handle);
    try {
        hyprmasq::initEis(handle);
        hyprmasq::selection::init(handle);
        hyprmasq::x11::init(handle);
        hyprmasq::deferred::init(handle);
        hyprmasq::cursor::init(handle);
        hyprmasq::launch::init();
        hyprmasq::placement::init(handle);
        hyprmasq::suspension::init(handle);
        hyprmasq::idle::init(handle);
    } catch (...) {
        shutdownAll();
        throw;
    }

#if HYPRMASQ_HYPRLAND >= 57
    HyprlandAPI::registerHyprCtlCommand(
        PHANDLE, IPC::Socket1::SCommand{.name = "hyprmasq", .match = IPC::Socket1::COMMAND_MATCH_PREFIX, .handler = [](const IPC::Socket1::SRequest& request) {
                                            return hyprmasq::handleCommand(request.format, request.command);
                                        }});
#else
    HyprlandAPI::registerHyprCtlCommand(PHANDLE, SHyprCtlCommand{.name = "hyprmasq", .exact = false, .fn = hyprmasq::handleCommand});
#endif

    return {"hyprmasq", "Per-window screenshots and EIS input for Hyprland", "nyrda", std::string{hyprmasq::VERSION}};
}

APICALL EXPORT void PLUGIN_EXIT() {
    shutdownAll();
    PHANDLE = nullptr;
}
