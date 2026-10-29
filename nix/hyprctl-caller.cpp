// A plugin that passes hyprctl requests on through HyprlandAPI::invokeHyprctlCommand, as another plugin calls hyprmasq,
// whose reply Hyprland's main thread waits for:
//
//   hyprctl [-j] hmcall <command> [arguments]

#include <hyprland/src/plugins/PluginAPI.hpp>

#include <string>

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

// The request is the whole line, starting with hmcall.
static IPC::Socket1::SResponse call(const IPC::Socket1::SRequest& parsed) {
    const auto& request = parsed.command;
    const auto  start   = request.find_first_not_of(' ', request.find(' '));
    if (start == std::string::npos)
        return "usage: hmcall <command> [arguments]";
    const auto end     = request.find(' ', start);
    const auto command = request.substr(start, end - start);
    const auto args    = end == std::string::npos ? std::string{} : request.substr(end + 1);
    return HyprlandAPI::invokeHyprctlCommand(command, args, parsed.format == IPC::Socket1::FORMAT_JSON ? "j" : "");
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    HyprlandAPI::registerHyprCtlCommand(handle, IPC::Socket1::SCommand{.name = "hmcall", .match = IPC::Socket1::COMMAND_MATCH_PREFIX, .handler = call});
    return {"hyprmasq-hyprctl-caller", "Calls hyprctl commands as a plugin does", "hyprmasq", "1"};
}

APICALL EXPORT void PLUGIN_EXIT() {}
