// A plugin that passes hyprctl requests on through HyprlandAPI::invokeHyprctlCommand, as another plugin calls hyprmasq,
// whose reply Hyprland's main thread waits for:
//
//   hyprctl [-j] hmcall <command> [arguments]

#include <hyprland/src/plugins/PluginAPI.hpp>

#include <string>

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

// The request is the whole line, starting with hmcall. Hyprland 0.56 has the older hyprctl API, see src/HyprlandVersion.hpp.
#if __has_include(<hyprland/src/ipc/s1/S1.hpp>)
static IPC::Socket1::SResponse call(const IPC::Socket1::SRequest& parsed) {
    const auto& request = parsed.command;
    const bool  json    = parsed.format == IPC::Socket1::FORMAT_JSON;
#else
static std::string call(eHyprCtlOutputFormat format, std::string request) {
    const bool json = format == FORMAT_JSON;
#endif
    const auto start = request.find_first_not_of(' ', request.find(' '));
    if (start == std::string::npos)
        return "usage: hmcall <command> [arguments]";
    const auto end     = request.find(' ', start);
    const auto command = request.substr(start, end - start);
    const auto args    = end == std::string::npos ? std::string{} : request.substr(end + 1);
    return HyprlandAPI::invokeHyprctlCommand(command, args, json ? "j" : "");
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
#if __has_include(<hyprland/src/ipc/s1/S1.hpp>)
    HyprlandAPI::registerHyprCtlCommand(handle, IPC::Socket1::SCommand{.name = "hmcall", .match = IPC::Socket1::COMMAND_MATCH_PREFIX, .handler = call});
#else
    HyprlandAPI::registerHyprCtlCommand(handle, SHyprCtlCommand{.name = "hmcall", .exact = false, .fn = call});
#endif
    return {"hyprmasq-hyprctl-caller", "Calls hyprctl commands as a plugin does", "hyprmasq", "1"};
}

APICALL EXPORT void PLUGIN_EXIT() {}
