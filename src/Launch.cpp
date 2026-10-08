#include "Launch.hpp"
#include "Process.hpp"
#include "Windows.hpp"

#include <hyprland/src/config/supplementary/executor/Executor.hpp>
#include <hyprland/src/desktop/rule/Engine.hpp>
#include <hyprland/src/desktop/rule/windowRule/WindowRule.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/event/EventBus.hpp>
#include <hyprland/src/helpers/time/Time.hpp>

#include <algorithm>
#include <chrono>
#include <format>
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
            std::string                    token;
            SP<Desktop::Rule::CWindowRule> rule; // places the window as it maps, until then
            std::string                    tag;  // that the rule matches
        };
        std::vector<STracked> tracked;
        unsigned              serial = 0;

        CHyprSignalListener   created, mapped, destroyed;

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

        // Tags the window and registers a window rule for that tag that puts it silently on the workspace as it maps.
        // Hyprland matches a tag on the window itself, while an exec rule would read the environment of the window's
        // process, which can wait for that process.
        void place(STracked& t, const PHLWINDOW& window, const std::string& workspace) {
            auto rule = Desktop::Rule::CWindowRule::buildFromExecString(std::format("workspace name:{} silent", workspace));
            if (!rule)
                return;
            t.tag = std::format("hyprmasq-{}", ++serial);
            window->m_ruleApplicator->m_tagKeeper.applyTag("+" + t.tag);
            (*rule)->registerMatch(Desktop::Rule::RULE_PROP_TAG, t.tag);
            t.rule = *rule;
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

        void onCreate(const PHLWINDOW& window) {
            if (!window || launches.empty())
                return;
            const pid_t self = getpid();
            const pid_t own  = window->getPID();
            for (pid_t pid = own, depth = 0; pid > 1 && pid != self && depth < MAX_DEPTH; pid = process::parent(pid), ++depth) {
                const auto launch = std::ranges::find_if(launches, [&](const auto& l) { return l.pid == pid; });
                if (launch == launches.end() || !alive(*launch))
                    continue;
                auto& t = tracked.emplace_back(STracked{.window = window, .token = launch->token, .rule = nullptr, .tag = ""});
                if (Time::steadyNow() - launch->at <= PLACE_WITHIN)
                    place(t, window, launch->workspace);
                return;
            }
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
        }
    }

    void init() {
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
        const auto pid = Config::Supplementary::executor()->spawnRaw(command);
        if (!pid || *pid <= 1)
            return std::unexpected(SError{eErrorCode::INTERNAL, "cannot start the command"});
        // A process that has already exited has no windows to find.
        launches.push_back({.pid = sc<pid_t>(*pid), .started = process::started(sc<pid_t>(*pid)).value_or(0), .token = token, .workspace = workspace, .at = Time::steadyNow()});
        return sc<pid_t>(*pid);
    }

    std::vector<SLaunched> windows() {
        std::erase_if(tracked, [](const auto& t) { return t.window.expired() && !t.rule; });
        std::vector<SLaunched> result;
        for (const auto& t : tracked)
            if (const auto window = t.window.lock(); window && window->m_isMapped)
                result.push_back({.address = windowAddress(window), .stableId = std::format("{:x}", window->m_stableID), .token = t.token});
        return result;
    }
}
