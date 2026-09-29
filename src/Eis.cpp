#include "Eis.hpp"
#include "Hooks.hpp"
#include "X11.hpp"
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/desktop/state/FocusState.hpp>
#include <hyprland/src/desktop/state/ViewState.hpp>
#include <hyprland/src/desktop/view/Popup.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/devices/IKeyboard.hpp>
#include <hyprland/src/managers/SeatManager.hpp>
#include <hyprland/src/managers/input/InputManager.hpp>
#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/protocols/XDGShell.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#include <hyprland/src/protocols/core/DataDevice.hpp>
#include <hyprland/src/protocols/core/Seat.hpp>
#include <hyprland/src/render/Renderer.hpp>
#include <libeis.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <algorithm>
#include <cerrno>
#include <cmath>
#include <memory>
#include <ranges>
#include <map>
#include <set>

namespace hyprmasq {
    namespace {
        struct Session;
        std::map<std::string, std::unique_ptr<Session>> sessions;
        void*                                           originalPopupGrab   = nullptr;
        void*                                           originalStartDrag   = nullptr;
        void*                                           originalActivateX11 = nullptr;
        void*                                           originalWindowFocus = nullptr;
        void*                                           originalActivate    = nullptr;

        // Include popup subsurfaces too. Hidden windows do not get compositor frame callbacks.
        void surfaces(const PHLWINDOW& w, const std::function<void(SP<CWLSurfaceResource>)>& fn) {
            auto visit = [&](SP<CWLSurfaceResource> s) {
                if (s)
                    s->breadthfirst([&](SP<CWLSurfaceResource> child, const Vector2D&, void*) { fn(child); }, nullptr);
            };
            visit(w->wlSurface()->resource());
            if (w->m_popupHead)
                w->m_popupHead->breadthfirst([&](SP<Desktop::View::CPopup> p, void*) { visit(p->resource()); }, nullptr);
        }

        struct Session {
            PHLWINDOWREF            window;
            std::string             path;
            eis*                    context  = nullptr;
            eis_client*             client   = nullptr;
            eis_seat*               seat     = nullptr;
            eis_device*             pointer  = nullptr;
            eis_device*             keyboard = nullptr;
            xkb_state*              state    = nullptr;
            wl_event_source*        fdSource = nullptr;
            wl_event_source*        timer    = nullptr;
            CHyprSignalListener     unmap, destroy, resize, keyboardFocus, pointerFocus;
            WP<CWLSurfaceResource>  enteredKeyboard, enteredPointer;
            std::vector<eis_event*> pending;
            std::set<uint32_t>      keys, buttons;
            Vector2D                position  = {1, 1};
            Time::steady_tp         lastInput = Time::steadyNow();
            bool                    force = false, paused = false, deferredKeys = false, restoreDue = false;
            Time::steady_tp         pausedAt, holdUntil = Time::steadyNow(), lastKeys = Time::steadyNow();
            eis_event*              stashed     = nullptr; // taken from libeis, waiting for a hold to end
            wl_client*              server      = nullptr; // the window's X server, looked up once
            bool                    serverKnown = false;

            Session(const PHLWINDOW& w, std::string p, bool f) : window(w), path(std::move(p)), force(f) {}
            ~Session() {
                clearClient();
                if (fdSource)
                    wl_event_source_remove(fdSource);
                if (timer)
                    wl_event_source_remove(timer);
                if (context)
                    eis_unref(context); // libeis owns and unlinks the socket and lock file
            }
            void clearDevice(eis_device*& d) {
                if (!d)
                    return;
                drop(d);
                if (d == pointer)
                    leavePointer();
                if (d == keyboard) {
                    leaveKeyboard();
                    keys.clear();
                    if (state)
                        xkb_state_unref(state);
                    state = nullptr;
                }
                eis_device_remove(d);
                eis_device_unref(d);
                d = nullptr;
            }
            void clearClient() {
                leave();
                clearDevice(pointer);
                clearDevice(keyboard);
                for (auto* e : pending)
                    eis_event_unref(e);
                pending.clear();
                if (state)
                    xkb_state_unref(state);
                state = nullptr;
                keys.clear();
                buttons.clear();
                if (seat)
                    eis_seat_unref(seat);
                seat         = nullptr;
                client       = nullptr;
                paused       = false;
                deferredKeys = restoreDue = false;
                holdUntil                 = Time::steadyNow();
                if (stashed)
                    eis_event_unref(stashed);
                stashed = nullptr;
            }
            wl_client* xServer() {
                if (!serverKnown) {
                    auto w = window.lock();
                    if (!w || !w->resource())
                        return nullptr;
                    server      = x11::server(w);
                    serverKnown = true;
                }
                return server;
            }
            SP<CWLSeatResource> wlSeat() {
                auto w = window.lock();
                auto s = w ? w->resource() : nullptr;
                return s ? g_pSeatManager->seatResourceForClient(s->client()) : nullptr;
            }
            void leaveKeyboard() {
                x11::release(window.lock());
                auto s = wlSeat();
                if (s && enteredKeyboard)
                    for (auto& k : s->m_keyboards)
                        if (k)
                            k->sendLeave();
                enteredKeyboard.reset();
            }
            void leavePointer() {
                auto s = wlSeat();
                if (s && enteredPointer)
                    for (auto& p : s->m_pointers)
                        if (p) {
                            // Release explicitly: Hyprland's sendLeave also releases held buttons.
                            for (auto button : buttons)
                                p->sendButton(Time::millis(Time::steadyNow()), button, WL_POINTER_BUTTON_STATE_RELEASED);
                            p->sendLeave();
                            p->sendFrame();
                        }
                enteredPointer.reset();
                buttons.clear();
            }
            void leave() {
                leaveKeyboard();
                leavePointer();
            }
            void makePointer() {
                if (pointer || !seat)
                    return;
                auto w = window.lock();
                if (!w)
                    return;
                pointer = eis_seat_new_device(seat);
                eis_device_configure_name(pointer, "hyprmasq window pointer");
                for (auto c : {EIS_DEVICE_CAP_POINTER_ABSOLUTE, EIS_DEVICE_CAP_BUTTON, EIS_DEVICE_CAP_SCROLL})
                    eis_device_configure_capability(pointer, c);
                auto       r    = eis_device_new_region(pointer);
                const auto size = w->size(Desktop::View::IGeometric::GEOMETRIC_GOAL);
                eis_region_set_size(r, std::max(1.0, size.x), std::max(1.0, size.y));
                eis_region_add(r);
                eis_region_unref(r);
                eis_device_add(pointer);
                if (!paused)
                    eis_device_resume(pointer);
            }
            void makeKeyboard() {
                auto kb = g_pSeatManager->m_keyboard.lock();
                if (keyboard || !seat || !kb || !kb->m_xkbKeymap)
                    return;
                keyboard = eis_seat_new_device(seat);
                eis_device_configure_name(keyboard, "hyprmasq window keyboard");
                eis_device_configure_capability(keyboard, EIS_DEVICE_CAP_KEYBOARD);
                auto km = eis_device_new_keymap(keyboard, EIS_KEYMAP_TYPE_XKB, kb->m_xkbKeymapFD.get(), kb->m_xkbKeymapString.size() + 1);
                if (!km) {
                    clearDevice(keyboard);
                    return;
                }
                eis_keymap_add(km);
                eis_keymap_unref(km);
                state = xkb_state_new(kb->m_xkbKeymap);
                if (!state) {
                    clearDevice(keyboard);
                    return;
                }
                eis_device_add(keyboard);
                if (!paused)
                    eis_device_resume(keyboard);
            }
            // X focus and stacking are shared by every window of one X server. While another of them has the real
            // focus, moving them would misroute input, so only the focused window itself or force may proceed.
            bool blocked() {
                auto  w = window.lock();
                auto* x = w && !force ? xServer() : nullptr;
                if (!x)
                    return false;
                auto active = Desktop::focusState()->window();
                if (active == w)
                    return false;
                if (active && active->resource() && active->resource()->client() == x)
                    return true;
                auto focused = g_pSeatManager->m_state.keyboardFocus.lock();
                return focused && focused->client() == x;
            }
            // Senders see paused devices, so they can tell that input was refused.
            void setPaused(bool p) {
                if (paused == p)
                    return;
                paused   = p;
                pausedAt = Time::steadyNow();
                for (auto* d : {pointer, keyboard}) {
                    if (d)
                        p ? eis_device_pause(d) : eis_device_resume(d);
                }
                // A pause that no focus change ends, after a failed X request, ends through tick().
                if (p)
                    wl_event_source_timer_update(timer, 500);
            }
            // libeis disconnects a sender whose events reach a resumed device before it saw the pause, so a sender still
            // busy sending when it was paused gets time to notice first.
            void maybeResume() {
                if (!paused || blocked())
                    return;
                const auto waited = std::chrono::duration_cast<std::chrono::milliseconds>(Time::steadyNow() - pausedAt).count();
                if (waited >= 500)
                    setPaused(false);
                else
                    wl_event_source_timer_update(timer, 500 - waited);
            }
            void drop(eis_device* device) {
                std::erase_if(pending, [&](eis_event* e) {
                    if (eis_event_get_device(e) != device)
                        return false;
                    eis_event_unref(e);
                    return true;
                });
            }
            // Stops taking events from the socket until `until`, 50 ms from now by default. libeis answers a sender's
            // ping only once it is taken, so senders still learn when everything has been delivered.
            void hold(std::optional<Time::steady_tp> until = std::nullopt) {
                if (auto w = window.lock(); w && w->resource())
                    wl_client_flush(w->resource()->client());
                holdUntil = until.value_or(Time::steadyNow() + std::chrono::milliseconds(50));
                lastInput = Time::steadyNow();
                wl_event_source_timer_update(timer, 16);
            }
            // xwayland-satellite drops keys that share a batch of Wayland events with the keyboard leave that follows
            // them. True if this event would send that leave too early.
            bool leavesSatelliteTooSoon(eis_event* e) {
                auto       w      = window.lock();
                const auto type   = eis_event_get_type(e);
                const bool leaves = (eis_event_get_device(e) == keyboard && (type == EIS_EVENT_DEVICE_STOP_EMULATING || type == EIS_EVENT_DEVICE_CLOSED)) ||
                    (type == EIS_EVENT_CLIENT_DISCONNECT && eis_event_get_client(e) == client);
                return leaves && enteredKeyboard && w && !w->m_isX11 && xServer() && Time::steadyNow() < lastKeys + std::chrono::milliseconds(50);
            }
            bool holding() const {
                return Time::steadyNow() < holdUntil;
            }
            void resumeHeld() {
                if (holding())
                    return;
                if (deferredKeys) {
                    deferredKeys = false;
                    if (keyboard)
                        deliver(keyboard);
                } else if (restoreDue) {
                    restoreDue = false;
                    auto s     = wlSeat();
                    auto real  = g_pSeatManager->m_state.keyboardFocus.lock();
                    if (s && enteredKeyboard && real && real->client() == s->client())
                        restoreKeyboard(s, real);
                }
                process();
            }
            void enterKeyboard(const SP<CWLSeatResource>& s, const SP<CWLSurfaceResource>& target, bool restore) {
                wl_array held;
                wl_array_init(&held);
                if (!restore && !keys.empty()) {
                    auto* dst = static_cast<uint32_t*>(wl_array_add(&held, keys.size() * sizeof(uint32_t)));
                    if (dst)
                        std::ranges::copy(keys, dst);
                }
                for (auto& k : s->m_keyboards)
                    if (k) {
                        k->sendLeave();
                        k->sendEnter(target, &held);
                    }
                wl_array_release(&held);
                enteredKeyboard = target;
                mods(s);
            }
            void restoreKeyboard(const SP<CWLSeatResource>& s, const SP<CWLSurfaceResource>& real) {
                auto     pressed = g_pInputManager->getKeysFromAllKBs();
                wl_array arr;
                wl_array_init(&arr);
                if (!pressed.empty()) {
                    auto* dst = static_cast<uint32_t*>(wl_array_add(&arr, pressed.size() * sizeof(uint32_t)));
                    if (dst)
                        std::ranges::copy(pressed, dst);
                }
                for (auto& k : s->m_keyboards)
                    if (k) {
                        k->sendLeave();
                        k->sendEnter(real, &arr);
                        if (auto kb = g_pSeatManager->m_keyboard.lock()) {
                            auto m = kb->m_modifiersState;
                            for (const auto& other : g_pInputManager->m_keyboards) {
                                if (!other->m_enabled || !other->shareStates() || (other->isVirtual() && g_pInputManager->shouldIgnoreVirtualKeyboard(other)))
                                    continue;
                                m.depressed |= other->m_modifiersState.depressed;
                                m.latched |= other->m_modifiersState.latched;
                                m.locked |= other->m_modifiersState.locked;
                            }
                            k->sendMods(m.depressed, m.latched, m.locked, m.group);
                        }
                    }
                wl_array_release(&arr);
                enteredKeyboard.reset();
            }
            void mods(const SP<CWLSeatResource>& s) {
                const auto dep   = xkb_state_serialize_mods(state, XKB_STATE_MODS_DEPRESSED);
                const auto lat   = xkb_state_serialize_mods(state, XKB_STATE_MODS_LATCHED);
                const auto lock  = xkb_state_serialize_mods(state, XKB_STATE_MODS_LOCKED);
                const auto group = xkb_state_serialize_layout(state, XKB_STATE_LAYOUT_EFFECTIVE);
                for (auto& k : s->m_keyboards)
                    if (k)
                        k->sendMods(dep, lat, lock, group);
                eis_device_keyboard_send_xkb_modifiers(keyboard, dep, lat, lock, group);
            }
            void deliver(eis_device* device) {
                auto w = window.lock();
                auto s = wlSeat();
                if (!w || !s) {
                    drop(device);
                    return;
                }
                if (std::ranges::none_of(pending, [device](eis_event* e) { return eis_event_get_device(e) == device; }))
                    return;
                if (blocked()) {
                    drop(device);
                    setPaused(true);
                    return;
                }
                // Another socket may address a second window on this same Wayland client.
                for (auto& [_, other] : sessions) {
                    if (other.get() != this && other->wlSeat() == s)
                        other->leave();
                }
                const bool isKeyboard = device == keyboard;
                // XWayland sends keys to the X focus and pointer events to the topmost X window under the pointer.
                // If the X server does not respond, the sender learns that input was dropped.
                std::optional<x11::SStack> stack;
                if (w->m_isX11 && (isKeyboard ? !x11::focus(w) : !(stack = x11::raise(w)))) {
                    drop(device);
                    setPaused(true);
                    return;
                }
                auto       real    = (isKeyboard ? g_pSeatManager->m_state.keyboardFocus : g_pSeatManager->m_state.pointerFocus).lock();
                const bool restore = real && real->client() == s->client();
                // xwayland-satellite moves X focus once it has handled the whole batch of Wayland events that entered the
                // window, but forwards keys at once. Keys and the return of focus to the user's window each wait for a
                // later batch, or they would reach whichever window had X focus before.
                const bool satellite = isKeyboard && !w->m_isX11 && real != w->resource() && xServer();
                if (satellite && !enteredKeyboard) {
                    enterKeyboard(s, w->resource(), restore);
                    deferredKeys = true;
                    hold();
                    return;
                }
                // Resource focus is client-local. Restore it before the event loop can dispatch real input.
                const auto now = Time::millis(Time::steadyNow());
                for (auto* e : pending) {
                    if (eis_event_get_device(e) != device)
                        continue;
                    const auto type = eis_event_get_type(e);
                    if (isKeyboard) {
                        if (!enteredKeyboard)
                            enterKeyboard(s, w->resource(), restore);
                        uint32_t code = eis_event_keyboard_get_key(e);
                        bool     down = eis_event_keyboard_get_key_is_press(e);
                        if (down ? !keys.insert(code).second : !keys.erase(code))
                            continue;
                        for (auto& k : s->m_keyboards)
                            if (k)
                                k->sendKey(now, code, down ? WL_KEYBOARD_KEY_STATE_PRESSED : WL_KEYBOARD_KEY_STATE_RELEASED);
                        lastKeys = Time::steadyNow();
                        xkb_state_update_key(state, code + 8, down ? XKB_KEY_DOWN : XKB_KEY_UP);
                        mods(s);
                    } else {
                        if (type == EIS_EVENT_POINTER_MOTION_ABSOLUTE) {
                            const double x = eis_event_pointer_get_absolute_x(e), y = eis_event_pointer_get_absolute_y(e);
                            if (!std::isfinite(x) || !std::isfinite(y))
                                continue;
                            const auto size = w->size(Desktop::View::IGeometric::GEOMETRIC_GOAL);
                            position        = {std::clamp(x, 0.0, std::max(0.0, size.x - 1)), std::clamp(y, 0.0, std::max(0.0, size.y - 1))};
                        }
                        if (type == EIS_EVENT_BUTTON_BUTTON) {
                            const auto button = eis_event_button_get_button(e);
                            const bool down   = eis_event_button_get_is_press(e);
                            // sendLeave already releases buttons during a same-client switch.
                            if (down ? buttons.contains(button) : !buttons.contains(button))
                                continue;
                        }
                        // Like Hyprland, address X11 windows through their one surface, in XWayland's scale.
                        Vector2D local  = position * w->m_X11SurfaceScaledBy;
                        auto     target = w->m_isX11 ? w->resource() :
                                                       Desktop::viewState()->hitTest().windowSurfaceAt(w->position(Desktop::View::IGeometric::GEOMETRIC_GOAL) + position, w, local);
                        if (!target)
                            continue;
                        if (enteredPointer != target) {
                            if (enteredPointer)
                                leavePointer();
                            for (auto& p : s->m_pointers)
                                if (p) {
                                    p->sendLeave();
                                    p->sendEnter(target, local);
                                }
                            enteredPointer = target;
                            buttons.clear();
                        }
                        for (auto& p : s->m_pointers) {
                            if (!p)
                                continue;
                            switch (type) {
                                case EIS_EVENT_POINTER_MOTION_ABSOLUTE: p->sendMotion(now, local); break;
                                case EIS_EVENT_BUTTON_BUTTON: {
                                    auto button = eis_event_button_get_button(e);
                                    bool down   = eis_event_button_get_is_press(e);
                                    p->sendButton(now, button, down ? WL_POINTER_BUTTON_STATE_PRESSED : WL_POINTER_BUTTON_STATE_RELEASED);
                                    if (down)
                                        g_pSeatManager->clearPointerButtonSerials(s, target, button);
                                    break;
                                }
                                case EIS_EVENT_SCROLL_DELTA:
                                case EIS_EVENT_SCROLL_DISCRETE:
                                case EIS_EVENT_SCROLL_STOP: {
                                    const bool   discrete = type == EIS_EVENT_SCROLL_DISCRETE;
                                    const double dx       = discrete ? eis_event_scroll_get_discrete_dx(e) : type == EIS_EVENT_SCROLL_DELTA ? eis_event_scroll_get_dx(e) : 0;
                                    const double dy       = discrete ? eis_event_scroll_get_discrete_dy(e) : type == EIS_EVENT_SCROLL_DELTA ? eis_event_scroll_get_dy(e) : 0;
                                    p->sendAxisSource(discrete ? WL_POINTER_AXIS_SOURCE_WHEEL : WL_POINTER_AXIS_SOURCE_CONTINUOUS);
                                    for (auto axis : {WL_POINTER_AXIS_HORIZONTAL_SCROLL, WL_POINTER_AXIS_VERTICAL_SCROLL}) {
                                        double v = axis == WL_POINTER_AXIS_HORIZONTAL_SCROLL ? dx : dy;
                                        if (std::isfinite(v) && v != 0) {
                                            if (discrete) {
                                                p->sendAxisValue120(axis, v);
                                                p->sendAxisDiscrete(axis, v / 120);
                                            }
                                            p->sendAxis(now, axis, discrete ? v / 12 : v);
                                        }
                                        if (type == EIS_EVENT_SCROLL_STOP &&
                                            (axis == WL_POINTER_AXIS_HORIZONTAL_SCROLL ? eis_event_scroll_get_stop_x(e) : eis_event_scroll_get_stop_y(e)))
                                            p->sendAxisStop(now, axis);
                                    }
                                    break;
                                }
                                default: break;
                            }
                        }
                        if (type == EIS_EVENT_BUTTON_BUTTON) {
                            if (eis_event_button_get_is_press(e))
                                buttons.insert(eis_event_button_get_button(e));
                            else
                                buttons.erase(eis_event_button_get_button(e));
                        }
                    }
                }
                drop(device);
                if (!isKeyboard)
                    for (auto& p : s->m_pointers)
                        if (p)
                            p->sendFrame();
                if (restore) {
                    // Queued frames go to the target first, and the focus goes back once they run out.
                    if (isKeyboard && satellite)
                        restoreDue = true;
                    else if (isKeyboard)
                        restoreKeyboard(s, real);
                    else {
                        auto       surf    = Desktop::View::CWLSurface::fromResource(real);
                        auto       box     = surf ? surf->getSurfaceBoxGlobal() : std::nullopt;
                        auto       realWin = surf ? Desktop::View::CWindow::fromView(surf->view()) : nullptr;
                        const auto scale   = realWin && realWin->m_isX11 ? realWin->m_X11SurfaceScaledBy : 1.0;
                        const auto local   = (g_pInputManager->getMouseCoordsInternal() - (box ? box->pos() : Vector2D{})) * scale;
                        leavePointer();
                        for (auto& p : s->m_pointers)
                            if (p) {
                                p->sendEnter(real, local);
                                p->sendFrame();
                            }
                        enteredPointer.reset();
                        buttons.clear();
                    }
                }
                if (stack)
                    x11::lower(*stack);
                // X focus goes back along with the Wayland keyboard focus.
                if (restore && isKeyboard)
                    x11::release(w);
                lastInput = Time::steadyNow();
                wl_event_source_timer_update(timer, 16);
            }
            int tick() {
                auto w = window.lock();
                if (!w)
                    return 0;
                maybeResume();
                resumeHeld();
                const auto now = Time::steadyNow();
                if (std::chrono::duration_cast<std::chrono::milliseconds>(now - lastInput).count() < 1000) {
                    if (!g_pHyprRenderer->shouldRenderWindow(w))
                        surfaces(w, [&](SP<CWLSurfaceResource> s) { s->frame(now); });
                    wl_event_source_timer_update(timer, 16);
                } else if (keys.empty() && buttons.empty())
                    leave();
                return 0;
            }
            int poll() {
                eis_dispatch(context);
                process();
                return 0;
            }
            void process() {
                while (!holding()) {
                    auto* e = stashed ? std::exchange(stashed, nullptr) : eis_get_event(context);
                    if (!e) {
                        if (restoreDue)
                            hold(lastKeys + std::chrono::milliseconds(50));
                        break;
                    }
                    if (leavesSatelliteTooSoon(e)) {
                        stashed = e;
                        hold(lastKeys + std::chrono::milliseconds(50));
                        break;
                    }
                    auto d = eis_event_get_device(e);
                    switch (eis_event_get_type(e)) {
                        case EIS_EVENT_CLIENT_CONNECT: {
                            auto c = eis_event_get_client(e);
                            if (!eis_client_is_sender(c)) {
                                eis_client_disconnect(c);
                                break;
                            }
                            // libeis can report a new sender before the exit of the previous one, so the newest one wins.
                            if (auto* previous = client) {
                                clearClient();
                                eis_client_disconnect(previous);
                            }
                            client = c;
                            eis_client_connect(c);
                            seat = eis_client_new_seat(c, "hyprmasq window");
                            for (auto cap : {EIS_DEVICE_CAP_POINTER_ABSOLUTE, EIS_DEVICE_CAP_BUTTON, EIS_DEVICE_CAP_SCROLL, EIS_DEVICE_CAP_KEYBOARD})
                                eis_seat_configure_capability(seat, cap);
                            eis_seat_add(seat);
                            break;
                        }
                        case EIS_EVENT_CLIENT_DISCONNECT:
                            if (eis_event_get_client(e) == client)
                                clearClient();
                            break;
                        case EIS_EVENT_SEAT_BIND:
                            if (eis_event_seat_has_capability(e, EIS_DEVICE_CAP_POINTER_ABSOLUTE) && eis_event_seat_has_capability(e, EIS_DEVICE_CAP_BUTTON) &&
                                eis_event_seat_has_capability(e, EIS_DEVICE_CAP_SCROLL))
                                makePointer();
                            else
                                clearDevice(pointer);
                            if (eis_event_seat_has_capability(e, EIS_DEVICE_CAP_KEYBOARD))
                                makeKeyboard();
                            else
                                clearDevice(keyboard);
                            break;
                        case EIS_EVENT_DEVICE_CLOSED:
                            if (d == pointer)
                                clearDevice(pointer);
                            if (d == keyboard)
                                clearDevice(keyboard);
                            break;
                        case EIS_EVENT_POINTER_MOTION_ABSOLUTE:
                        case EIS_EVENT_BUTTON_BUTTON:
                        case EIS_EVENT_SCROLL_DELTA:
                        case EIS_EVENT_SCROLL_DISCRETE:
                        case EIS_EVENT_SCROLL_STOP:
                        case EIS_EVENT_KEYBOARD_KEY:
                            if (d != pointer && d != keyboard)
                                break;
                            if (pending.size() < 4096)
                                pending.push_back(eis_event_ref(e));
                            else if (client)
                                eis_client_disconnect(client);
                            break;
                        case EIS_EVENT_FRAME:
                            if (d == pointer || d == keyboard)
                                deliver(d);
                            break;
                        case EIS_EVENT_DEVICE_STOP_EMULATING:
                            if (d == pointer || d == keyboard) {
                                if (d == keyboard)
                                    leaveKeyboard();
                                if (d == keyboard && state) {
                                    for (auto code : keys)
                                        xkb_state_update_key(state, code + 8, XKB_KEY_UP);
                                    keys.clear();
                                }
                                if (d == pointer)
                                    leavePointer();
                                drop(d);
                            }
                            break;
                        default: break;
                    }
                    eis_event_unref(e);
                }
            }
        };

        bool protectedSurface(const SP<CWLSurfaceResource>& surface) {
            for (auto& [_, session] : sessions) {
                auto w = session->window.lock();
                if (!w || w == Desktop::focusState()->window())
                    continue;
                bool found = false;
                surfaces(w, [&](SP<CWLSurfaceResource> s) { found |= s == surface; });
                if (found)
                    return true;
            }
            return false;
        }
        void popupGrab(CXDGShellProtocol* self, SP<CXDGPopupResource> popup) {
            // The new popup need not be mapped or in the window's popup tree yet.
            auto parent = popup->m_parent.lock();
            while (parent) {
                if (protectedSurface(parent->m_surface.lock()))
                    return;
                parent = parent->m_popup ? parent->m_popup->m_parent.lock() : nullptr;
            }
            using Fn = void (*)(CXDGShellProtocol*, SP<CXDGPopupResource>);
            reinterpret_cast<Fn>(originalPopupGrab)(self, popup);
        }
        void startDrag(CWLDataDeviceProtocol* self, WP<CWLDataSourceResource> source, SP<CWLSurfaceResource> icon, SP<CWLSurfaceResource> origin) {
            if (protectedSurface(origin)) {
                if (source)
                    source->cancelled();
                return;
            }
            using Fn = void (*)(CWLDataDeviceProtocol*, WP<CWLDataSourceResource>, SP<CWLSurfaceResource>, SP<CWLSurfaceResource>);
            reinterpret_cast<Fn>(originalStartDrag)(self, source, icon, origin);
        }

        // X11 has no popup grabs to refuse. An X11 app takes focus by asking for activation, or by mapping a window
        // that Hyprland focuses on map. Refuse both for apps with a driven window while the user is in another app.
        // xwayland-satellite windows all come from one process, and an XWayland window need not tell its process. For
        // activation, those count as one app per X server. New windows are only kept from focus by a known process, so
        // that apps the user starts still get focus.
        bool sameX11App(const PHLWINDOW& a, wl_client* aServer, const PHLWINDOW& b, wl_client* bServer, bool knownProcess) {
            const auto pid = [](const PHLWINDOW& w) { return w->m_isX11 ? w->getPID() : 0; };
            if (!aServer || aServer != bServer)
                return false;
            if (pid(a) > 0 && pid(b) > 0)
                return pid(a) == pid(b);
            return !knownProcess;
        }
        bool drivenX11App(const PHLWINDOW& w, bool knownProcess) {
            auto* server  = x11::server(w);
            auto  focused = Desktop::focusState()->window();
            if (!server || (focused && sameX11App(w, server, focused, x11::server(focused), knownProcess)))
                return false;
            return std::ranges::any_of(sessions, [&](const auto& entry) {
                auto driven = entry.second->window.lock();
                return driven && sameX11App(w, server, driven, entry.second->xServer(), knownProcess);
            });
        }
        void activateX11(Desktop::View::CWindow* self) {
            if (drivenX11App(self->m_self.lock(), false)) {
                x11::refuseActivation(self->m_self.lock());
                return;
            }
            using Fn = void (*)(Desktop::View::CWindow*);
            reinterpret_cast<Fn>(originalActivateX11)(self);
        }
        // xwayland-satellite forwards activation requests as xdg-activation. A forced activation comes from the user,
        // for example through a taskbar.
        void activate(Desktop::View::CWindow* self, bool force) {
            if (!force && drivenX11App(self->m_self.lock(), false))
                return;
            using Fn = void (*)(Desktop::View::CWindow*, bool);
            reinterpret_cast<Fn>(originalActivate)(self, force);
        }
        void windowFocus(Desktop::CFocusState* self, PHLWINDOW w, Desktop::eFocusReason reason, SP<CWLSurfaceResource> surface) {
            if (w && reason == Desktop::FOCUS_REASON_NEW_WINDOW && drivenX11App(w, true))
                return;
            using Fn = void (*)(Desktop::CFocusState*, PHLWINDOW, Desktop::eFocusReason, SP<CWLSurfaceResource>);
            reinterpret_cast<Fn>(originalWindowFocus)(self, w, reason, surface);
        }

        // Refuse symlinks and pre-existing directories accessible to another user.
        int privateDir(int parent, const char* name) {
            if (mkdirat(parent, name, 0700) < 0 && errno != EEXIST)
                return -1;
            int         fd = openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            struct stat st{};
            if (fd >= 0 && (fstat(fd, &st) < 0 || st.st_uid != getuid() || (st.st_mode & 0777) != 0700)) {
                close(fd);
                return -1;
            }
            return fd;
        }
    }

    void initEis(void* handle) {
        originalPopupGrab   = hook(handle, "CXDGShellProtocol::addOrStartGrab", reinterpret_cast<void*>(&popupGrab));
        originalStartDrag   = hook(handle, "CWLDataDeviceProtocol::initiateDrag", reinterpret_cast<void*>(&startDrag));
        originalActivateX11 = hook(handle, "Desktop::View::CWindow::activateX11", reinterpret_cast<void*>(&activateX11));
        originalWindowFocus = hook(handle, "Desktop::CFocusState::rawWindowFocus", reinterpret_cast<void*>(&windowFocus));
        originalActivate    = hook(handle, "Desktop::View::CWindow::activate", reinterpret_cast<void*>(&activate));
    }
    void shutdownEis() {
        sessions.clear();
    }
    std::expected<std::string, std::string> openEis(const PHLWINDOW& w, bool force) {
        const auto address = windowAddress(w);
        if (sessions.contains(address)) {
            auto& session = *sessions.at(address);
            session.force = force;
            session.maybeResume();
            return session.path;
        }
        auto kb = g_pSeatManager->m_keyboard.lock();
        if (!kb || !kb->m_xkbKeymap || !kb->m_xkbKeymapFD.isValid())
            return std::unexpected("an active Wayland seat keyboard with a keymap is required");
        const char* runtime = getenv("XDG_RUNTIME_DIR");
        if (!runtime || runtime[0] != '/')
            return std::unexpected("XDG_RUNTIME_DIR must be absolute");
        int base     = open(runtime, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        int root     = base < 0 ? -1 : privateDir(base, "hyprmasq");
        int instance = root < 0 ? -1 : privateDir(root, g_pCompositor->m_instanceSignature.c_str());
        if (base >= 0)
            close(base);
        if (root >= 0)
            close(root);
        if (instance < 0)
            return std::unexpected("cannot create private hyprmasq socket directory");
        close(instance);
        auto  session = std::make_unique<Session>(w, std::string(runtime) + "/hyprmasq/" + g_pCompositor->m_instanceSignature + "/" + address + ".eis", force);
        auto* s       = session.get();
        s->context    = eis_new(nullptr);
        if (!s->context || eis_setup_backend_socket(s->context, s->path.c_str()) != 0)
            return std::unexpected("cannot create EIS socket (check path length and existing sockets)");
        // Enforce permissions on the pathname too, independently of libeis or the process umask.
        if (chmod(s->path.c_str(), 0600) != 0)
            return std::unexpected("cannot restrict EIS socket permissions");
        s->fdSource = wl_event_loop_add_fd(
            g_pCompositor->m_wlEventLoop, eis_get_fd(s->context), WL_EVENT_READABLE, [](int, uint32_t, void* data) { return static_cast<Session*>(data)->poll(); }, s);
        s->timer = wl_event_loop_add_timer(g_pCompositor->m_wlEventLoop, [](void* data) { return static_cast<Session*>(data)->tick(); }, s);
        if (!s->fdSource || !s->timer)
            return std::unexpected("cannot register EIS event sources");
        s->unmap         = w->m_events.unmap.listen([address] { closeEis(address); });
        s->destroy       = w->m_events.destroy.listen([address] { closeEis(address); });
        s->resize        = w->m_events.resize.listen([s] {
            if (s->pointer) {
                s->clearDevice(s->pointer);
                s->makePointer();
            }
        });
        s->keyboardFocus = g_pSeatManager->m_events.keyboardFocusChange.listen([s] {
            s->enteredKeyboard.reset();
            s->maybeResume();
        });
        s->pointerFocus  = g_pSeatManager->m_events.pointerFocusChange.listen([s] { s->enteredPointer.reset(); });
        auto path        = s->path;
        sessions.emplace(address, std::move(session));
        return path;
    }
    bool closeEis(const std::string& address) {
        return sessions.erase(address) != 0;
    }
    std::vector<SEisInfo> listEis() {
        std::vector<SEisInfo> result;
        for (const auto& [address, session] : sessions)
            result.push_back({.address = address, .path = session->path, .force = session->force});
        return result;
    }
}
