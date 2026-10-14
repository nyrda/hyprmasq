#include "Placement.hpp"
#include "Hooks.hpp"
#include "Launch.hpp"
#include "X11.hpp"

#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/config/values/types/BoolValue.hpp>
#include <hyprland/src/desktop/rule/windowRule/WindowRuleApplicator.hpp>
#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/event/EventBus.hpp>
#include <hyprland/src/managers/SeatManager.hpp>
#include <hyprland/src/managers/input/InputManager.hpp>
#include <hyprland/src/managers/input/TextInput.hpp>
#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>

#include <linux/input-event-codes.h>

#include <algorithm>
#include <chrono>
#include <stdexcept>
#include <utility>
#include <vector>

namespace hyprmasq::placement {
    namespace {
        // How long after emulated input a new window of the same app without a parent counts as opened by it.
        constexpr auto RECENT = std::chrono::seconds(5);

        // An app as input reaches it: its Wayland connection and process, and for a window of Hyprland's XWayland, which
        // serves every X11 app on one connection, the process of the X11 window. The process tells the connection from a
        // later one at the same address, once the first has gone, which a closed window's record can outlive.
        struct SApp {
            wl_client* client                        = nullptr;
            pid_t      pid                           = 0;
            bool       operator==(const SApp&) const = default;
        };

        // A window the agent works in: one that got emulated input, or that the plugin placed. What the last emulated
        // input reached is kept for RECENT after the window closes: a dialog that input closes can make its app open
        // another window right after, such as a progress window after Send.
        struct SDriven {
            PHLWINDOWREF                   window;
            std::optional<Time::steady_tp> input   = {}; // the last emulated input
            std::optional<Time::steady_tp> trigger = {}; // the last emulated input that can open a window, see triggers
            Time::steady_tp                agent   = {}; // the last emulated input, or when the plugin placed it
            std::optional<Time::steady_tp> user    = {}; // the last real input since it is on the list
            // Of the window when the last emulated input reached it.
            std::optional<SApp> app       = {};
            std::string         address   = {};
            PHLWORKSPACEREF     workspace = {};
            PHLWINDOWREF        parent    = {};
        };

        struct SOpened {
            PHLWINDOWREF window;
            SSource      source;
        };

        SP<Config::Values::CBoolValue> enabled;
        std::vector<SDriven>           driven;
        // The last real input to each app that can open a window, see triggers, of the last RECENT.
        std::vector<std::pair<SApp, Time::steady_tp>> userInput;
        std::vector<SOpened>                          opened;
        void*                                         originalReadStaticRules = nullptr;
        CHyprSignalListener                           key, button, touch, tip;
        wl_event_source*                              afterRefocus = nullptr;

        void                                          prune() {
            const auto now = Time::steadyNow();
            std::erase_if(driven, [&](const SDriven& d) { return d.window.expired() && (!d.input || now - *d.input >= RECENT); });
            std::erase_if(opened, [](const SOpened& o) { return o.window.expired(); });
        }

        SDriven* find(const PHLWINDOW& window) {
            const auto it = std::ranges::find_if(driven, [&](const SDriven& d) { return d.window.lock() == window; });
            return it == driven.end() ? nullptr : &*it;
        }

        std::optional<SApp> appOf(const PHLWINDOW& w) {
            const auto surface = w->resource();
            if (!surface)
                return std::nullopt;
            SApp app{.client = surface->client(), .pid = w->getPID()};
            if (w->m_isX11 && app.pid <= 0)
                return std::nullopt;
            return app;
        }

        // Real input: a key, button, touch or tablet tip that Hyprland handles, to the surface with the keyboard or
        // pointer focus. Emulated input goes to the window's seat resources directly and never passes here. Any input
        // counts for a window the agent works in that it reaches; only a trigger counts for the app.
        void realInput(const SP<CWLSurfaceResource>& surface, bool trigger) {
            if (!surface)
                return;
            const auto now = Time::steadyNow();
            SApp       app{.client = surface->client()};
            wl_client_get_credentials(app.client, &app.pid, nullptr, nullptr);
            for (const auto& w : Desktop::windowState()->windows()) {
                if (w && w->m_isX11 && w->m_isMapped && w->resource() == surface) {
                    app.pid = w->getPID();
                    break;
                }
            }
            std::erase_if(userInput, [&](const auto& entry) { return (trigger && entry.first == app) || now - entry.second >= RECENT; });
            if (trigger)
                userInput.emplace_back(app, now);
            for (auto& d : driven) {
                const auto w = d.window.lock();
                if (!w || !w->resource() || w->resource()->client() != app.client)
                    continue;
                bool reaches = false;
                forEachSurface(w, [&](SP<CWLSurfaceResource> s) { reaches |= s == surface; });
                if (reaches)
                    d.user = now;
            }
        }

        // Hyprland focuses the window under a touch or a tablet tip only after the event, so the input counts for the
        // keyboard focus once Hyprland has handled it.
        void realInputAfterRefocus() {
            if (afterRefocus)
                return;
            afterRefocus = wl_event_loop_add_idle(
                g_pCompositor->m_wlEventLoop,
                [](void*) {
                    afterRefocus = nullptr; // libwayland removes it after this call
                    realInput(g_pSeatManager->m_state.keyboardFocus.lock(), true);
                },
                nullptr);
        }

        // A real key that can open a window: see triggers. Enter in a text field, which the app tells through
        // text-input, types a line break.
        bool realKeyTriggers(const IKeyboard::SKeyEvent& event) {
            if (event.state != WL_KEYBOARD_KEY_STATE_PRESSED)
                return false;
            const bool shortcut = g_pInputManager->getModsFromAllKBs() & (HL_MODIFIER_CTRL | HL_MODIFIER_ALT | HL_MODIFIER_META);
            if (!shortcut && (event.keycode == KEY_ENTER || event.keycode == KEY_KPENTER)) {
                auto* const field = g_pInputManager->m_relay.getFocusedTextInput();
                if (field && field->isEnabled())
                    return false;
            }
            return triggers(event.keycode, shortcut);
        }

        struct SCandidate {
            std::string  address;
            PHLWINDOWREF window;
            PHLWORKSPACE workspace;
            bool         yours = false; // the user's own input came later, so Hyprland places it
        };

        // The window a new window came from, if it is one the agent works in.
        std::optional<SCandidate> candidate(const PHLWINDOW& w) {
            // A parent names it: an xdg_toplevel parent, WM_TRANSIENT_FOR, or through xdg-foreign a window of another
            // app, as for a portal's file chooser. A parent the agent does not work in, such as the user's own window
            // of the same app, makes the window the user's.
            if (const auto parent = w->parent()) {
                const auto* d = find(parent);
                if (!d)
                    return std::nullopt;
                return SCandidate{.address = windowAddress(parent), .window = parent, .workspace = parent->m_workspace, .yours = d->user && *d->user >= d->agent};
            }
            // Without one, the window of its app that got emulated input last, within RECENT, also one that has closed
            // since: on its workspace, or if that is gone, on that of its parent. The windows of an xwayland-satellite
            // share its connection and its process, so its apps cannot be told apart.
            const auto app = appOf(w);
            if (!app || (!w->m_isX11 && x11::server(w)))
                return std::nullopt;
            const auto                     now  = Time::steadyNow();
            const SDriven*                 best = nullptr;
            PHLWORKSPACE                   workspace;
            std::optional<Time::steady_tp> trigger; // the agent's last input to the app that can open a window
            for (const auto& d : driven) {
                const auto source = d.window.lock();
                if (source == w || !d.input || now - *d.input >= RECENT || d.app != app)
                    continue;
                if (d.trigger && now - *d.trigger < RECENT && (!trigger || *d.trigger > *trigger))
                    trigger = d.trigger;
                if (best && *d.input <= *best->input)
                    continue;
                PHLWORKSPACE at;
                if (source && source->m_isMapped)
                    at = source->m_workspace;
                else if (!(at = d.workspace.lock()))
                    if (const auto parent = d.parent.lock(); parent && parent->m_isMapped)
                        at = parent->m_workspace;
                if (at) {
                    best      = &d;
                    workspace = at;
                }
            }
            if (!best)
                return std::nullopt;
            // The user's input of any kind to that window, or a trigger of theirs to the app, against the agent's: so
            // their typing into another window of the app does not take a window that the agent's click opened, while
            // their click there takes one that the agent's typing came after.
            const auto user  = std::ranges::find_if(userInput, [&](const auto& entry) { return entry.first == *app; });
            const bool yours = (best->user && *best->user >= *best->input) || (user != userInput.end() && user->second >= trigger.value_or(*best->input));
            return SCandidate{.address = best->address, .window = best->window, .workspace = workspace, .yours = yours};
        }

        // Before Hyprland reads a window's rules: at a Wayland window's first commit, before Hyprland suggests a size for
        // it, where an app has usually named the parent, and again when Hyprland maps the window, where the parent and the
        // X11 process are known. Hyprland creates the window before the app can name either.
        void decide(const PHLWINDOW& w) {
            if (!enabled)
                return;
            prune();
            // A window that a launch placed is one the agent works in, from then on.
            if (launch::placed(w) && !find(w))
                driven.push_back(SDriven{.window = w, .agent = Time::steadyNow()});
            const auto found  = candidate(w);
            const bool usable = found && found->workspace;
            const bool yours  = usable && found->yours;
            if (!usable || yours || !enabled->value()) {
                // What the first commit decided does not hold: the window is no longer one the agent works in, unless
                // emulated input reached it or a launch placed it.
                launch::withdraw(w);
                std::erase_if(driven, [&](const SDriven& d) { return d.window.lock() == w && !d.input && !launch::placed(w); });
            }
            std::erase_if(opened, [&](const SOpened& o) { return o.window.lock() == w; });
            if (!usable)
                return;
            SSource source{.address = found->address, .window = found->window};
            if (!enabled->value())
                source.reason = "disabled";
            else if (yours)
                source.reason = "user_input";
            else if (!launch::placeOn(w, found->workspace))
                source.reason = "workspace"; // it cannot be named in a rule
            else {
                source.placed = true;
                if (auto* d = find(w))
                    d->agent = Time::steadyNow();
                else
                    driven.push_back(SDriven{.window = w, .agent = Time::steadyNow()});
            }
            opened.push_back({.window = w, .source = source});
        }

        // The window of a rule applicator is private. An explicit instantiation may name private members, so this
        // reaches it without redefining access specifiers around the include.
        PHLWINDOWREF& windowOf(Desktop::Rule::CWindowRuleApplicator& applicator);
        template <PHLWINDOWREF Desktop::Rule::CWindowRuleApplicator::* Window>
        struct SApplicatorAccess {
            friend PHLWINDOWREF& windowOf(Desktop::Rule::CWindowRuleApplicator& applicator) {
                return applicator.*Window;
            }
        };
        template struct SApplicatorAccess<&Desktop::Rule::CWindowRuleApplicator::m_window>;

        bool readStaticRules(Desktop::Rule::CWindowRuleApplicator* self, bool preRead) {
            if (const auto w = windowOf(*self).lock())
                decide(w);
            using Fn = bool (*)(Desktop::Rule::CWindowRuleApplicator*, bool);
            return reinterpret_cast<Fn>(originalReadStaticRules)(self, preRead);
        }
    }

    void init(void* handle) {
        enabled = makeShared<Config::Values::CBoolValue>("plugin:hyprmasq:place_new_windows",
                                                         "open windows that emulated input opens on the workspace of the window that got it, without focus", true);
        if (!HyprlandAPI::addConfigValueV2(handle, enabled))
            throw std::runtime_error("hyprmasq: cannot register config values");
        auto& input = Event::bus()->m_events.input;
        key         = input.keyboard.key.listen(
            [](const IKeyboard::SKeyEvent& event, Event::SCallbackInfo&) { realInput(g_pSeatManager->m_state.keyboardFocus.lock(), realKeyTriggers(event)); });
        button = input.mouse.button.listen([](const IPointer::SButtonEvent&, Event::SCallbackInfo&) { realInput(g_pSeatManager->m_state.pointerFocus.lock(), true); });
        touch  = input.touch.down.listen([](const ITouch::SDownEvent&, Event::SCallbackInfo&) { realInputAfterRefocus(); });
        tip    = input.tablet.tip.listen([](const CTablet::STipEvent&, Event::SCallbackInfo&) { realInputAfterRefocus(); });
        originalReadStaticRules = hook(handle, "Desktop::Rule::CWindowRuleApplicator::readStaticRules", reinterpret_cast<void*>(&readStaticRules));
    }

    void shutdown() {
        key.reset();
        button.reset();
        touch.reset();
        tip.reset();
        if (afterRefocus)
            wl_event_source_remove(afterRefocus);
        afterRefocus = nullptr;
        driven.clear();
        userInput.clear();
        opened.clear();
        enabled.reset();
    }

    bool triggers(uint32_t keycode, bool shortcut) {
        switch (keycode) {
            // Modifiers alone.
            case KEY_LEFTCTRL:
            case KEY_RIGHTCTRL:
            case KEY_LEFTSHIFT:
            case KEY_RIGHTSHIFT:
            case KEY_LEFTALT:
            case KEY_RIGHTALT:
            case KEY_LEFTMETA:
            case KEY_RIGHTMETA:
            case KEY_CAPSLOCK:
            case KEY_NUMLOCK: return false;
            default: break;
        }
        if (shortcut)
            return true;
        // Keys that type or edit text, or move in it.
        const bool typing = (keycode >= KEY_ESC && keycode <= KEY_RIGHTBRACE) || (keycode >= KEY_A && keycode <= KEY_GRAVE) || (keycode >= KEY_BACKSLASH && keycode <= KEY_SLASH) ||
            keycode == KEY_KPASTERISK || keycode == KEY_SPACE || (keycode >= KEY_KP7 && keycode <= KEY_KPDOT) || keycode == KEY_102ND || keycode == KEY_KPSLASH ||
            (keycode >= KEY_HOME && keycode <= KEY_DELETE) || keycode == KEY_KPEQUAL || keycode == KEY_RO || keycode == KEY_YEN || keycode == KEY_KPCOMMA ||
            keycode == KEY_KPJPCOMMA;
        return !typing;
    }

    void emulatedInput(const PHLWINDOW& window, bool trigger) {
        prune();
        const auto now = Time::steadyNow();
        auto*      d   = find(window);
        if (!d)
            d = &driven.emplace_back(SDriven{.window = window});
        if (trigger)
            d->trigger = now;
        d->input     = now;
        d->agent     = now;
        d->app       = appOf(window);
        d->address   = windowAddress(window);
        d->workspace = window->m_workspace;
        d->parent    = window->parent();
    }

    std::optional<SSource> source(const PHLWINDOW& window) {
        const auto it = std::ranges::find_if(opened, [&](const SOpened& o) { return o.window.lock() == window; });
        return it == opened.end() ? std::nullopt : std::optional{it->source};
    }
}
