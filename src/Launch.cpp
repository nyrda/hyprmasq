#include "Launch.hpp"
#include "Hooks.hpp"
#include "Process.hpp"
#include "Windows.hpp"
#include <hyprland/src/Compositor.hpp>

#include <hyprland/src/config/supplementary/executor/Executor.hpp>
#include <hyprland/src/debug/log/Logger.hpp>
#include <hyprland/src/desktop/Workspace.hpp>
#include <hyprland/src/desktop/rule/Engine.hpp>
#include <hyprland/src/desktop/rule/windowRule/WindowRule.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/event/EventBus.hpp>
#include <hyprland/src/helpers/time/Time.hpp>

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <dlfcn.h>
#include <filesystem>
#include <format>
#include <optional>
#include <ranges>
#include <unistd.h>

namespace hyprmasq::launch {
    namespace {
        // The parents followed from a window's process: a launcher script, a shell and the shell of a desktop file are
        // a few levels.
        constexpr int MAX_DEPTH = 32;
        // Like Hyprland's exec rules, a launch places the windows of its first minute, so that a window the app opens
        // later, when the user may have moved it elsewhere, or that a program started in a launched shell opens, opens
        // where Hyprland and the user's rules put it.
        constexpr auto   PLACE_WITHIN = std::chrono::minutes(1);
        constexpr size_t MAX_LAUNCHES = 256;

        struct SLaunch {
            pid_t              pid     = 0;
            unsigned long long started = 0; // tells the process from a later one with its pid
            std::string        token, workspace;
            Time::steady_tp    at;
        };
        std::vector<SLaunch> launches;

        struct STracked {
            PHLWINDOWREF                   window;
            std::string                    token; // of its launch, empty for a window only placed next to its opener
            SP<Desktop::Rule::CWindowRule> rule;  // places the window as it maps, until then
            std::string                    tag;   // that the rule matches
            bool                           placed    = false;
            bool                           opened    = false; // placed by placeOn, not by a launch
            bool                           floating  = false;
            std::string                    workspace = {}; // as the rule names it
        };
        std::vector<STracked> tracked;
        unsigned              serial = 0;

        CHyprSignalListener   created, mapped, destroyed;

        // hooks::launch, through which CWindow::activate keeps the windows that the plugin placed from taking the focus,
        // while one of them lives or a launch may still place one, and the timer that looks again once a launch's
        // minute has passed.
        std::optional<CHookClaim> launchHooks;
        wl_event_source*          launchesEnd = nullptr;

        // hyprmasq-reaper, which the command of a launch runs under, or empty when there is none.
        std::string reaper;

        bool executable(const std::filesystem::path& path) {
            return path.is_absolute() && access(path.c_str(), X_OK) == 0;
        }

        // Next to the plugin, as make builds both, in ../libexec of the plugin's directory, as the Nix package installs
        // it, or on PATH.
        std::string findReaper() {
            Dl_info info;
            if (dladdr(reinterpret_cast<void*>(&findReaper), &info) && info.dli_fname) {
                const auto dir = std::filesystem::path(info.dli_fname).parent_path();
                for (const auto& candidate : {dir / "hyprmasq-reaper", dir.parent_path() / "libexec" / "hyprmasq-reaper"})
                    if (executable(candidate))
                        return candidate.string();
            }
            const char* path = std::getenv("PATH");
            for (const auto& dir : std::string_view(path ? path : "") | std::views::split(':'))
                if (const auto candidate = std::filesystem::path(std::string_view(dir)) / "hyprmasq-reaper"; executable(candidate))
                    return candidate.string();
            return "";
        }

        // One argument for /bin/sh.
        std::string shellQuoted(std::string_view arg) {
            std::string out = "'";
            for (const char c : arg) {
                if (c == '\'')
                    out += "'\\''";
                else
                    out += c;
            }
            return out + "'";
        }

        // Names that came from a hyprctl request must not reach a rule as anything but a workspace name or a token.
        bool validToken(std::string_view token) {
            return !token.empty() && token.size() <= 64 && std::ranges::all_of(token, [](char c) { return std::isxdigit(sc<unsigned char>(c)); });
        }

        bool validWorkspace(std::string_view name) {
            return !name.empty() && name.size() <= 64 && std::ranges::all_of(name, [](char c) { return std::isalnum(sc<unsigned char>(c)) || c == '_' || c == '.' || c == '-'; });
        }

        bool alive(const SLaunch& launch) {
            return process::started(launch.pid) == launch.started;
        }

        // The workspace as a window rule names it: a numbered one by its number, others by their name, which has to
        // keep to the characters of a launch's workspace.
        std::optional<std::string> selector(const PHLWORKSPACE& workspace) {
            if (!workspace)
                return std::nullopt;
            if (workspace->m_isSpecialWorkspace) {
                const auto& name = workspace->m_name;
                if (name.starts_with("special:") && validWorkspace(std::string_view(name).substr(8)))
                    return name;
                return std::nullopt;
            }
            if (workspace->m_id > 0)
                return std::to_string(workspace->m_id);
            if (validWorkspace(workspace->m_name))
                return "name:" + workspace->m_name;
            return std::nullopt;
        }

        std::optional<SError> claimHooks() {
            if (launchHooks)
                return std::nullopt;
            auto claim = hooks::launch.claim();
            if (!claim)
                return claim.error();
            launchHooks = std::move(*claim);
            return std::nullopt;
        }

        // Releases the hooks once no window that the plugin placed lives and no launch places any more.
        void reconsiderHooks() {
            const auto                     now     = Time::steadyNow();
            const bool                     windows = std::ranges::any_of(tracked, [](const auto& t) { return t.placed && !t.window.expired(); });
            std::optional<Time::steady_tp> placing;
            for (const auto& launch : launches)
                if (now - launch.at <= PLACE_WITHIN && alive(launch))
                    placing = std::max(placing.value_or(launch.at), launch.at);
            if (placing && launchesEnd)
                wl_event_source_timer_update(launchesEnd, sc<int>(std::chrono::ceil<std::chrono::milliseconds>(*placing + PLACE_WITHIN - now).count()) + 1);
            if (!windows && !placing)
                launchHooks.reset();
        }

        // Tags the window and registers a window rule for that tag that puts it silently on the workspace as it maps,
        // floating at the size it asks for when floating is set. Hyprland matches a tag on the window itself, while an
        // exec rule would read the environment of the window's process, which can wait for that process. Hyprland drops
        // a workspace rule, silent included, that names the workspace the window maps on anyway, the active one, so
        // no_initial_focus keeps the focus there too.
        void place(STracked& t, const PHLWINDOW& window, const std::string& workspace, bool floating) {
            auto rule = Desktop::Rule::CWindowRule::buildFromExecString(std::format("workspace {} silent;no_initial_focus 1{}", workspace, floating ? ";float" : ""));
            if (!rule)
                return;
            // Held already for a launch, and for input that opens a window, while its socket is in use. A window that
            // opens later still opens silently, and may take the focus by asking for activation.
            if (const auto refused = claimHooks())
                Log::logger->log(Log::WARN, "[hyprmasq] placing a window that may take the focus: {}", refused->message);
            t.tag = std::format("hyprmasq-{}", ++serial);
            window->m_ruleApplicator->m_tagKeeper.applyTag("+" + t.tag);
            (*rule)->registerMatch(Desktop::Rule::RULE_PROP_TAG, t.tag);
            t.rule      = *rule;
            t.placed    = true;
            t.floating  = floating;
            t.workspace = workspace;
            Desktop::Rule::ruleEngine()->registerRule(SP<Desktop::Rule::IRule>(*rule));
        }

        // Removes the rule and the tag once the window has mapped or will not map.
        void unplace(STracked& t, const PHLWINDOW& window) {
            if (t.rule)
                Desktop::Rule::ruleEngine()->unregisterRule(t.rule);
            if (window && !t.tag.empty())
                window->m_ruleApplicator->m_tagKeeper.applyTag("-" + t.tag);
            t.rule.reset();
            t.tag.clear();
        }

        const SLaunch* launchOf(const PHLWINDOW& window) {
            const pid_t self = getpid();
            for (pid_t pid = window->getPID(), depth = 0; pid > 1 && pid != self && depth < MAX_DEPTH; pid = process::parent(pid), ++depth) {
                const auto launch = std::ranges::find_if(launches, [&](const auto& l) { return l.pid == pid; });
                if (launch != launches.end() && alive(*launch))
                    return &*launch;
            }
            return nullptr;
        }

        // A window of a launch, also of a child process. Windows that emulated input opens are placed later, see
        // placeOn, once their parent is known.
        void onCreate(const PHLWINDOW& window) {
            const auto* launch = window && !launches.empty() ? launchOf(window) : nullptr;
            if (!launch)
                return;
            auto& t = tracked.emplace_back(STracked{.window = window, .token = launch->token, .rule = nullptr, .tag = ""});
            if (Time::steadyNow() - launch->at <= PLACE_WITHIN)
                place(t, window, "name:" + launch->workspace, false);
        }

        void onMapped(const PHLWINDOW& window) {
            for (auto& t : tracked)
                if (t.rule && t.window.lock() == window)
                    unplace(t, window);
        }

        void onDestroyed(const PHLWINDOWREF& ref) {
            const auto window = ref.lock();
            for (auto& t : tracked)
                if (t.rule && (window ? t.window.lock() == window : t.window.expired()))
                    unplace(t, window);
            std::erase_if(tracked, [](const auto& t) { return t.window.expired() && !t.rule; });
            // The window is still there while Hyprland destroys it, so it counts until the next event.
            if (launchesEnd)
                wl_event_source_timer_update(launchesEnd, 1);
        }
    }

    void init() {
        reaper = findReaper();
        if (reaper.empty())
            Log::logger->log(Log::WARN, "[hyprmasq] hyprmasq-reaper not found next to the plugin or on PATH: a launch loses the windows of an app whose launcher exits");
        else
            Log::logger->log(Log::DEBUG, "[hyprmasq] launching through {}", reaper);
        launchesEnd = wl_event_loop_add_timer(
            g_pCompositor->m_wlEventLoop,
            [](void*) {
                reconsiderHooks();
                return 0;
            },
            nullptr);
        created   = Event::bus()->m_events.window.create.listen([](PHLWINDOW window) { onCreate(window); });
        mapped    = Event::bus()->m_events.window.openEarly.listen([](PHLWINDOW window) { onMapped(window); });
        destroyed = Event::bus()->m_events.window.destroy.listen([](PHLWINDOWREF window) { onDestroyed(window); });
    }

    void shutdown() {
        created.reset();
        mapped.reset();
        destroyed.reset();
        for (auto& t : tracked)
            unplace(t, t.window.lock());
        tracked.clear();
        launches.clear();
        if (launchesEnd)
            wl_event_source_remove(launchesEnd);
        launchesEnd = nullptr;
        launchHooks.reset();
    }

    std::expected<pid_t, SError> start(const std::string& workspace, const std::string& token, const std::string& command) {
        if (!validWorkspace(workspace))
            return std::unexpected(SError{eErrorCode::INVALID_ARGUMENTS, "the workspace may only contain letters, digits, '_', '.' and '-'"});
        if (!validToken(token))
            return std::unexpected(SError{eErrorCode::INVALID_ARGUMENTS, "the token must be 1 to 64 hex digits"});
        if (command.empty())
            return std::unexpected(SError{eErrorCode::INVALID_ARGUMENTS, "no command"});
        std::erase_if(launches, [](const auto& l) { return !alive(l); });
        if (launches.size() >= MAX_LAUNCHES)
            launches.erase(launches.begin());
        // Nothing starts that could take the user's focus.
        if (auto refused = claimHooks())
            return std::unexpected(std::move(*refused));
        // Under hyprmasq-reaper, the started process stays, as the parent of the command's processes whose parents exit, as
        // the GUI of an app whose launcher forks it away and exits, until the last of them has exited, so launchOf finds
        // it from their windows too.
        const auto pid = Config::Supplementary::executor()->spawnRaw(reaper.empty() ? command : std::format("exec {} /bin/sh -c {}", shellQuoted(reaper), shellQuoted(command)));
        if (!pid || *pid <= 1) {
            reconsiderHooks();
            return std::unexpected(SError{eErrorCode::INTERNAL, "cannot start the command"});
        }
        // A process that has already exited has no windows to find.
        launches.push_back({.pid = sc<pid_t>(*pid), .started = process::started(sc<pid_t>(*pid)).value_or(0), .token = token, .workspace = workspace, .at = Time::steadyNow()});
        reconsiderHooks();
        return sc<pid_t>(*pid);
    }

    std::vector<SLaunched> windows() {
        std::erase_if(tracked, [](const auto& t) { return t.window.expired() && !t.rule; });
        std::vector<SLaunched> result;
        for (const auto& t : tracked)
            if (const auto window = t.window.lock(); window && window->m_isMapped && !t.token.empty())
                result.push_back({.address = windowAddress(window), .stableId = std::format("{:x}", window->m_stableID), .token = t.token});
        return result;
    }

    bool placeOn(const PHLWINDOW& window, const PHLWORKSPACE& workspace) {
        const auto name = selector(workspace);
        if (!window || !name)
            return false;
        const auto pending = std::ranges::find_if(tracked, [&](const auto& t) { return t.rule && t.window.lock() == window; });
        if (pending != tracked.end()) {
            // An earlier placeOn on this workspace, or a launch's rule, which places it on the launch's workspace and
            // only has to make it float there, as a window that input opens.
            if (!pending->opened || pending->workspace == *name) {
                if (!pending->floating) {
                    const auto on = pending->workspace;
                    unplace(*pending, window);
                    pending->placed = false;
                    place(*pending, window, on, true);
                }
                return pending->placed;
            }
            unplace(*pending, window);
            pending->placed = false;
        }
        auto& t = tracked.emplace_back(STracked{.window = window, .token = "", .rule = nullptr, .tag = "", .opened = true, .workspace = *name});
        // It floats, for now: tiled, it would take half of the space of the window the agent works in, which then has
        // to catch up with a new size, and on a visible workspace it would move the user's windows.
        place(t, window, *name, true);
        return t.placed;
    }

    void withdraw(const PHLWINDOW& window) {
        bool withdrawn = false;
        for (auto& t : tracked)
            if (t.opened && t.rule && t.window.lock() == window) {
                unplace(t, window);
                t.placed  = false;
                withdrawn = true;
            }
        // It may have been the last placed window that needs the hooks.
        if (withdrawn && launchesEnd)
            wl_event_source_timer_update(launchesEnd, 1);
    }

    bool placed(const PHLWINDOW& window) {
        return window && std::ranges::any_of(tracked, [&](const auto& t) { return t.placed && t.window.lock() == window; });
    }
}
