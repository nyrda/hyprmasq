#include "Placement.hpp"
#include "Hooks.hpp"
#include "Launch.hpp"
#include "PrivateAccess.hpp"
#include "Process.hpp"
#include "X11.hpp"

#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/config/values/types/BoolValue.hpp>
#include <hyprland/src/desktop/rule/windowRule/WindowRuleApplicator.hpp>
#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/devices/IKeyboard.hpp>
#include <hyprland/src/event/EventBus.hpp>
#include <hyprland/src/managers/SeatManager.hpp>
#include <hyprland/src/managers/input/InputManager.hpp>
#include <hyprland/src/managers/input/TextInput.hpp>
#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/protocols/InputMethodV2.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>

#include <linux/input-event-codes.h>
#include <xkbcommon/xkbcommon-names.h>
#include <xkbcommon/xkbcommon.h>

#include <algorithm>
#include <chrono>
#include <span>
#include <stdexcept>
#include <utility>
#include <vector>

namespace hyprmasq::placement {
    namespace {
        // How long after emulated input a new window of the same app without a parent counts as opened by it.
        constexpr auto RECENT = std::chrono::seconds(5);
        // How soon after the user lets go of a key that typed text Enter to the same surface still types a line break: a
        // pause within typing, shorter than the 2 s from which writing research counts a pause.
        constexpr auto LINE = std::chrono::seconds(2);

        // An app as input reaches it: its Wayland connection and process, and for a window of Hyprland's XWayland, which
        // serves every X11 app on one connection, the process of the X11 window. The process tells the connection from a
        // later one at the same address, once the first has gone, which a closed window's record can outlive. A process
        // on Wayland connections of its own is one app on all of them: Electron draws its windows on one and opens GTK's
        // file chooser on another. For those, when the process started tells it from a later process with its pid, which
        // the record can outlive too. An X server's connection has none, since one process serves many apps there.
        struct SApp {
            wl_client*                        client  = nullptr;
            pid_t                             pid     = 0;
            std::optional<unsigned long long> started = {};
            bool operator==(const SApp&) const        = default;
        };

        // The same process, by when it started where both are on connections of their own, and otherwise on the same
        // connection.
        bool sameApp(const SApp& a, const SApp& b) {
            return a.pid == b.pid && (a.started && b.started ? a.started == b.started : a.client == b.client);
        }

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

        // The windows the agent works in and the user's last input to each app that can open a window, at one moment.
        struct SInputs {
            Time::steady_tp                                   at;
            std::span<const SDriven>                          driven;
            std::span<const std::pair<SApp, Time::steady_tp>> userInput;
        };

        // What a Wayland window's first commit found, for the decision when it maps: the app had created the window by
        // then, so no later input opened it.
        struct SCreated {
            PHLWINDOWREF                                  window;
            Time::steady_tp                               at;
            std::vector<SDriven>                          driven;
            std::vector<std::pair<SApp, Time::steady_tp>> userInput;
        };

        // The surface that the user's last key press typed text into, and when.
        WP<CWLSurfaceResource>         typedInto;
        Time::steady_tp                typedAt;
        SP<Config::Values::CBoolValue> enabled;
        std::vector<SDriven>           driven;
        // The last real input to each app that can open a window, see triggers, of the last RECENT.
        std::vector<std::pair<SApp, Time::steady_tp>> userInput;
        std::vector<SOpened>                          opened;
        std::vector<SCreated>                         created;
        // The hooked function, see SPinned at the end of this file.
        using ReadStaticRules                             = bool (Desktop::Rule::CWindowRuleApplicator::*)(bool);
        HookFn<ReadStaticRules>   originalReadStaticRules = nullptr;
        std::optional<CHookClaim> installed;
        CHyprSignalListener       key, button, touch, tip;
        wl_event_source*          afterRefocus = nullptr;

        void prune() {
            const auto now = Time::steadyNow();
            std::erase_if(driven, [&](const SDriven& d) { return d.window.expired() && (!d.input || now - *d.input >= RECENT); });
            std::erase_if(opened, [](const SOpened& o) { return o.window.expired(); });
            std::erase_if(created, [](const SCreated& c) { return c.window.expired(); });
        }

        const SDriven* find(std::span<const SDriven> list, const PHLWINDOW& window) {
            const auto it = std::ranges::find_if(list, [&](const SDriven& d) { return d.window.lock() == window; });
            return it == list.end() ? nullptr : &*it;
        }

        SDriven* find(const PHLWINDOW& window) {
            const auto it = std::ranges::find_if(driven, [&](const SDriven& d) { return d.window.lock() == window; });
            return it == driven.end() ? nullptr : &*it;
        }

        // When the process of a connection of its own started, or none for an X server's connection, from known when
        // it is for the same connection and process.
        std::optional<unsigned long long> startedOf(const SApp& app, const std::optional<SApp>& known = std::nullopt) {
            if (known && known->client == app.client && known->pid == app.pid)
                return known->started;
            if (app.pid <= 0 || x11::serves(app.client))
                return std::nullopt;
            return process::started(app.pid);
        }

        std::optional<SApp> appOf(const PHLWINDOW& w, const std::optional<SApp>& known = std::nullopt) {
            const auto surface = w->resource();
            if (!surface)
                return std::nullopt;
            SApp app{.client = surface->client(), .pid = w->getPID()};
            if (w->m_isX11 && app.pid <= 0)
                return std::nullopt;
            app.started = startedOf(app, known);
            return app;
        }

        // Real input: a key, button, touch or tablet tip that Hyprland handles, to the surface with the keyboard or
        // pointer focus. Emulated input goes to the window's seat resources directly and never passes here. Any input
        // counts for a window the agent works in that it reaches; only a trigger counts for the app, and ends the typing
        // that a line break follows.
        void realInput(const SP<CWLSurfaceResource>& surface, bool trigger) {
            if (trigger)
                typedInto.reset();
            // The user's input counts against the agent's that came before it. With no window the agent works in, any
            // input of the agent's comes later, so there is nothing to note.
            if (!surface || driven.empty())
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
            if (trigger)
                app.started = startedOf(app);
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

        // Modifiers alone.
        bool modifier(uint32_t keycode) {
            switch (keycode) {
                case KEY_LEFTCTRL:
                case KEY_RIGHTCTRL:
                case KEY_LEFTSHIFT:
                case KEY_RIGHTSHIFT:
                case KEY_LEFTALT:
                case KEY_RIGHTALT:
                case KEY_LEFTMETA:
                case KEY_RIGHTMETA:
                case KEY_CAPSLOCK:
                case KEY_NUMLOCK: return true;
                default: return false;
            }
        }

        // Keys that type or edit text, or move in it.
        bool editing(uint32_t keycode) {
            return (keycode >= KEY_ESC && keycode <= KEY_RIGHTBRACE) || (keycode >= KEY_A && keycode <= KEY_GRAVE) || (keycode >= KEY_BACKSLASH && keycode <= KEY_SLASH) ||
                keycode == KEY_KPASTERISK || keycode == KEY_SPACE || (keycode >= KEY_KP7 && keycode <= KEY_KPDOT) || keycode == KEY_102ND || keycode == KEY_KPSLASH ||
                (keycode >= KEY_HOME && keycode <= KEY_DELETE) || keycode == KEY_KPEQUAL || keycode == KEY_RO || keycode == KEY_YEN || keycode == KEY_KPCOMMA ||
                keycode == KEY_KPJPCOMMA;
        }

        // Whether Num Lock is on, by the LED of the keyboard that has the key down, which each keyboard records before
        // Hyprland emits the key, or else of the seat's keyboard.
        bool numLock(uint32_t keycode) {
            SP<IKeyboard> keyboard;
            for (const auto& k : g_pInputManager->m_keyboards) {
                if (k && k->getPressed(keycode)) {
                    keyboard = k;
                    break;
                }
            }
            if (!keyboard)
                keyboard = g_pSeatManager->m_keyboard.lock();
            return keyboard && keyboard->m_xkbState && xkb_state_led_name_is_active(keyboard->m_xkbState, XKB_LED_NAME_NUM) > 0;
        }

        // Keys that type a character or delete one: of the editing keys, all but Escape, Tab and those that move, and the
        // keypad's digits and point only while Num Lock is on, since they move otherwise.
        bool typesText(uint32_t keycode) {
            const bool keypad = keycode >= KEY_KP7 && keycode <= KEY_KPDOT && keycode != KEY_KPMINUS && keycode != KEY_KPPLUS;
            return editing(keycode) && keycode != KEY_ESC && keycode != KEY_TAB && !(keycode >= KEY_HOME && keycode <= KEY_INSERT) && (!keypad || numLock(keycode));
        }

        // Whether the app got the press of a key that is being released: Hyprland emits the release before it forwards it,
        // and keeps the keys it forwarded until then, but not one that a keybind or input capture took. Through an input
        // method's grab, keys reach the app by the input method.
        bool delivered(uint32_t keycode) {
            const auto inputMethod = g_pInputManager->m_relay.m_inputMethod.lock();
            return (inputMethod && inputMethod->hasGrab()) || std::ranges::contains(g_pInputManager->getKeysFromAllKBs(), keycode);
        }

        // A real key that can open a window: see triggers. Enter types a line break instead in a text field, which the app
        // tells through text-input, and less than LINE after the user let go of a key that typed text into the same
        // surface, as in an app without text-input, such as Chromium. Any other key ends the typing, but modifiers alone and
        // line breaks, and so does a key that typed nothing since Hyprland kept it from the app.
        bool realKeyTriggers(const IKeyboard::SKeyEvent& event) {
            const auto now     = Time::steadyNow();
            const auto surface = g_pSeatManager->m_state.keyboardFocus.lock();
            if (event.state != WL_KEYBOARD_KEY_STATE_PRESSED) {
                // The app repeats a held key until it is let go, so the typing goes on until then. A key that the app did
                // not get typed nothing.
                if (surface && typedInto == surface && typesText(event.keycode)) {
                    if (delivered(event.keycode))
                        typedAt = now;
                    else
                        typedInto.reset();
                }
                return false;
            }
            const bool shortcut = g_pInputManager->getModsFromAllKBs() & (HL_MODIFIER_CTRL | HL_MODIFIER_ALT | HL_MODIFIER_META);
            if (!shortcut && (event.keycode == KEY_ENTER || event.keycode == KEY_KPENTER)) {
                auto* const field = g_pInputManager->m_relay.getFocusedTextInput();
                if ((field && field->isEnabled()) || (surface && typedInto == surface && now - typedAt < LINE))
                    return false;
            }
            if (!shortcut && typesText(event.keycode)) {
                typedInto = surface;
                typedAt   = now;
            } else if (!modifier(event.keycode))
                typedInto.reset();
            return triggers(event.keycode, shortcut);
        }

        struct SCandidate {
            std::string  address;
            PHLWINDOWREF window;
            PHLWORKSPACE workspace;
            bool         yours = false; // the user's own input came later, so Hyprland places it
        };

        // The window a new window came from, if it is one the agent works in, by the input at one moment.
        std::optional<SCandidate> candidate(const PHLWINDOW& w, const SInputs& inputs) {
            // A parent names it: an xdg_toplevel parent, WM_TRANSIENT_FOR, or through xdg-foreign a window of another
            // app, as for a portal's file chooser. A parent the agent does not work in, such as the user's own window
            // of the same app, makes the window the user's.
            if (const auto parent = w->parent()) {
                const auto* d = find(inputs.driven, parent);
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
            const SDriven*                 best = nullptr;
            PHLWORKSPACE                   workspace;
            std::optional<Time::steady_tp> trigger; // the agent's last input to the app that can open a window
            for (const auto& d : inputs.driven) {
                const auto source = d.window.lock();
                if (source == w || !d.input || inputs.at - *d.input >= RECENT || !d.app || !sameApp(*d.app, *app))
                    continue;
                if (d.trigger && inputs.at - *d.trigger < RECENT && (!trigger || *d.trigger > *trigger))
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
            std::optional<Time::steady_tp> user;
            for (const auto& [from, at] : inputs.userInput)
                if (sameApp(from, *app) && (!user || at > *user))
                    user = at;
            const bool yours = (best->user && *best->user >= *best->input) || (user && *user >= trigger.value_or(*best->input));
            return SCandidate{.address = best->address, .window = best->window, .workspace = workspace, .yours = yours};
        }

        // Before Hyprland reads a window's rules: at a Wayland window's first commit, before Hyprland suggests a size for
        // it, where an app has usually named the parent, and again when Hyprland maps the window, where the parent and the
        // X11 process are known. Hyprland creates the window before the app can name either. The decision when a Wayland
        // window maps counts the input up to its first commit, which is when the app had created it; that of an X11
        // window, which Hyprland learns of when it maps, the input up to then, and so does that of a Wayland window that
        // nothing before its first commit can have opened, as one that an app creates ahead and maps on later input.
        void decide(const PHLWINDOW& w, bool firstCommit) {
            if (!enabled)
                return;
            prune();
            const auto now = Time::steadyNow();
            // A window that a launch placed is one the agent works in, from then on.
            if (launch::placed(w) && !find(w))
                driven.push_back(SDriven{.window = w, .agent = now});
            const SInputs             live{.at = now, .driven = driven, .userInput = userInput};
            const auto                first = std::ranges::find_if(created, [&](const SCreated& c) { return c.window.lock() == w; });
            std::optional<SCandidate> found;
            if (firstCommit) {
                if (first != created.end())
                    created.erase(first);
                created.push_back(SCreated{.window = w, .at = now, .driven = driven, .userInput = userInput});
                found = candidate(w, live);
            } else if (first != created.end()) {
                found = candidate(w, {.at = first->at, .driven = first->driven, .userInput = first->userInput});
                created.erase(first);
                if (!found)
                    found = candidate(w, live);
            } else
                found = candidate(w, live);
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
                    d->agent = now;
                else
                    driven.push_back(SDriven{.window = w, .agent = now});
            }
            opened.push_back({.window = w, .source = source});
        }

        bool readStaticRules(Desktop::Rule::CWindowRuleApplicator* self, bool preRead) {
            if (const auto w = windowOf(*self).lock())
                decide(w, preRead);
            return originalReadStaticRules(self, preRead);
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
        // A window that emulated input opened can map any time after the input, so the hook stays for the plugin's
        // lifetime. Without it, such windows open where Hyprland puts them.
        hooks::placement.add(defineHook<ReadStaticRules, &readStaticRules>(handle, "_ZN7Desktop4Rule21CWindowRuleApplicator15readStaticRulesEb",
                                                                           "Desktop::Rule::CWindowRuleApplicator::readStaticRules(bool)", originalReadStaticRules));
        installed = hooks::forLifetime(hooks::placement);
    }

    void shutdown() {
        installed.reset();
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
        created.clear();
        typedInto.reset();
        enabled.reset();
    }

    bool triggers(uint32_t keycode, bool shortcut) {
        return !modifier(keycode) && (shortcut || !editing(keycode));
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
        d->app       = appOf(window, d->app);
        d->address   = windowAddress(window);
        d->workspace = window->m_workspace;
        d->parent    = window->parent();
    }

    std::optional<SSource> source(const PHLWINDOW& window) {
        const auto it = std::ranges::find_if(opened, [&](const SOpened& o) { return o.window.lock() == window; });
        return it == opened.end() ? std::nullopt : std::optional{it->source};
    }
}

template struct hyprmasq::SPinned<hyprmasq::placement::ReadStaticRules, &Desktop::Rule::CWindowRuleApplicator::readStaticRules>;
