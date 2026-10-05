#include "Eis.hpp"
#include "Cursor.hpp"
#include "Hooks.hpp"
#include "Wait.hpp"
#include "X11.hpp"
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/desktop/state/FocusState.hpp>
#include <hyprland/src/desktop/view/Popup.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/devices/IKeyboard.hpp>
#include <hyprland/src/event/EventBus.hpp>
#include <hyprland/src/managers/SeatManager.hpp>
#include <hyprland/src/managers/input/InputManager.hpp>
#include <hyprland/src/output/Monitor.hpp>
#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/protocols/XDGShell.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#include <hyprland/src/protocols/core/DataDevice.hpp>
#include <hyprland/src/protocols/core/Seat.hpp>
#include <hyprland/src/render/Renderer.hpp>
#include <libeis.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <fcntl.h>
#include <unistd.h>
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <format>
#include <memory>
#include <ranges>
#include <map>
#include <random>
#include <set>
#include <utility>

namespace hyprmasq {
    namespace {
        struct Session;
        std::map<std::string, std::unique_ptr<Session>> sessions;
        void*                                           originalPopupGrab     = nullptr;
        void*                                           originalStartDrag     = nullptr;
        void*                                           originalActivateX11   = nullptr;
        void*                                           originalWindowFocus   = nullptr;
        void*                                           originalActivate      = nullptr;
        void*                                           originalUpdateDecos   = nullptr;
        void*                                           originalKeyboardFocus = nullptr;
        void*                                           originalPointerFocus  = nullptr;
        CHyprSignalListener                             monitorLayout;

        // Socket ids: random for each load of the plugin, then counting.
        uint64_t socketNonce = 0, socketCount = 0;

        // The surface a keyboard or pointer resource sends its events to is private to Hyprland. An explicit
        // instantiation may name private members, so these reach it without redefining access specifiers around the
        // include.
        WP<CWLSurfaceResource>& surfaceOf(CWLKeyboardResource& keyboard);
        WP<CWLSurfaceResource>& surfaceOf(CWLPointerResource& pointer);
        template <WP<CWLSurfaceResource> CWLKeyboardResource::* Keyboard, WP<CWLSurfaceResource> CWLPointerResource::* Pointer>
        struct SFocusAccess {
            friend WP<CWLSurfaceResource>& surfaceOf(CWLKeyboardResource& keyboard) {
                return keyboard.*Keyboard;
            }
            friend WP<CWLSurfaceResource>& surfaceOf(CWLPointerResource& pointer) {
                return pointer.*Pointer;
            }
        };
        template struct SFocusAccess<&CWLKeyboardResource::m_currentSurface, &CWLPointerResource::m_currentSurface>;

        // True if focus is on a surface of the window's Wayland connection.
        bool sharesConnection(const PHLWINDOW& w, const WP<CWLSurfaceResource>& focus) {
            auto surface = focus.lock();
            return surface && w->resource() && surface->client() == w->resource()->client();
        }

        struct SLease {
            Time::steady_tp until;
            std::string     owner;
        };

        struct SPointerRegion {
            uint32_t x = 0, y = 0, width = 0, height = 0;
            bool     operator==(const SPointerRegion&) const = default;
        };

        // The pointer device's regions in window-local logical pixels: the window, then the rest of the bounding box of
        // the window and its monitor right of and below it. libeis region offsets are unsigned, so the parts left of or
        // above the window are left out, and window-local coordinates stay the device's coordinates.
        std::vector<SPointerRegion> pointerRegions(const PHLWINDOW& w) {
            const auto     size  = w->size(Desktop::View::IGeometric::GEOMETRIC_GOAL);
            const uint32_t width = std::max(1.0, size.x), height = std::max(1.0, size.y);
            uint32_t       right = width, bottom = height;
            if (const auto monitor = w->m_monitor.lock()) {
                const auto far = monitor->m_position + monitor->m_size - w->position(Desktop::View::IGeometric::GEOMETRIC_GOAL);
                right          = std::clamp(std::ceil(far.x), sc<double>(width), 1e6);
                bottom         = std::clamp(std::ceil(far.y), sc<double>(height), 1e6);
            }
            std::vector<SPointerRegion> regions{{.x = 0, .y = 0, .width = width, .height = height}};
            if (right > width)
                regions.push_back({.x = width, .y = 0, .width = right - width, .height = height});
            if (bottom > height)
                regions.push_back({.x = 0, .y = height, .width = right, .height = bottom - height});
            return regions;
        }

        // Shots show a window's popups where the renderer draws them: less the window's xdg geometry offset, which
        // Hyprland's popup hit testing leaves out. Points are looked up in the frame of the shots, so that input reaches
        // what they show there. The offset of the window's own surface is 0 in both.
        Vector2D popupOffset(const PHLWINDOW& w) {
            return !w->m_isX11 && w->m_xdgSurface ? w->m_xdgSurface->m_current.geometry.pos() : Vector2D{};
        }

        // The popup at a window-local point. Hyprland places popups at the window's animated position when it tests for
        // them, so the point is given there too, and a window that moves finds its popups where shots show them.
        SP<Desktop::View::CPopup> popupAt(const PHLWINDOW& w, const Vector2D& point) {
            if (w->m_isX11 || !w->m_popupHead || w->m_popupHead->popupTreeCount() == 0)
                return nullptr;
            const auto popup = w->m_popupHead->at(w->position(Desktop::View::IGeometric::GEOMETRIC_CURRENT) + popupOffset(w) + point);
            return popup && popup->wlSurface() && popup->wlSurface()->resource() ? popup : nullptr;
        }

        // The surface at a window-local point, and the point in that surface's coordinates.
        SP<CWLSurfaceResource> surfaceAt(const PHLWINDOW& w, const Vector2D& point, Vector2D& local) {
            // Like Hyprland, address X11 windows through their one surface, in XWayland's scale.
            if (w->m_isX11) {
                local = point * w->m_X11SurfaceScaledBy;
                return w->resource();
            }
            if (const auto popup = popupAt(w, point)) {
                local = popupOffset(w) + point - popup->coordsRelativeToParent();
                return popup->wlSurface()->resource();
            }
            const auto surface = w->resource();
            if (!surface)
                return nullptr;
            const auto [found, at] = surface->at(point, true);
            local                  = at;
            return found;
        }

        // Input events, which a frame then delivers, and frames.
        bool carriesInput(eis_event* e) {
            switch (eis_event_get_type(e)) {
                case EIS_EVENT_POINTER_MOTION_ABSOLUTE:
                case EIS_EVENT_BUTTON_BUTTON:
                case EIS_EVENT_SCROLL_DELTA:
                case EIS_EVENT_SCROLL_DISCRETE:
                case EIS_EVENT_SCROLL_STOP:
                case EIS_EVENT_KEYBOARD_KEY:
                case EIS_EVENT_FRAME: return true;
                default: return false;
            }
        }

        struct Session {
            PHLWINDOWREF            window;
            std::string             path;
            std::string             id       = std::format("{:x}-{}", socketNonce, ++socketCount); // see SEisInfo
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
            // The window's commits from the socket's opening, for wait.
            std::shared_ptr<wait::CContentWatch> content;
            uint64_t                             commitsAtInput = 0;
            bool                                 force = false, paused = false, deferredKeys = false, restoreDue = false;
            Time::steady_tp                      pausedAt, holdUntil = Time::steadyNow(), lastKeys = Time::steadyNow(), lastPing;
            eis_event*                           stashed     = nullptr; // taken from libeis, waiting for a hold to end
            wl_client*                           server      = nullptr; // the window's X server, looked up once
            bool                                 serverKnown = false;
            std::vector<SPointerRegion>          regions; // of the pointer device, as announced
            wl_event_source*                     regionCheck = nullptr;
            std::optional<SLease>                lease;
            // Set while Hyprland changes the real focus and a lease keeps this window's emulated focus, see CKeptFocus.
            bool keepingKeyboard = false, keepingPointer = false;

            Session(const PHLWINDOW& w, std::string p, bool f) : window(w), path(std::move(p)), content(std::make_shared<wait::CContentWatch>(w)), force(f) {}
            ~Session() {
                lease.reset(); // the emulated focus ends with the socket
                clearClient();
                releaseX();
                if (fdSource)
                    wl_event_source_remove(fdSource);
                if (timer)
                    wl_event_source_remove(timer);
                if (regionCheck)
                    wl_event_source_remove(regionCheck);
                if (context)
                    eis_unref(context); // libeis owns and unlinks the socket and lock file
            }
            void clearDevice(eis_device*& d) {
                if (!d)
                    return;
                drop(d);
                if (d == pointer)
                    releasePointer();
                if (d == keyboard) {
                    releaseKeyboard();
                    if (state)
                        xkb_state_unref(state);
                    state = nullptr;
                }
                eis_device_remove(d);
                eis_device_unref(d);
                d = nullptr;
            }
            void clearClient() {
                // Buttons first, so that the modifiers held around them still apply to the release.
                releasePointer();
                releaseKeyboard();
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
                scheduleIdle();
            }
            // tick() stops while keys or buttons are held. Once they are let go without further input, it has to run
            // again to give X focus back.
            void scheduleIdle() {
                if (timer)
                    wl_event_source_timer_update(timer, 16);
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
            // X focus outlives the sender, which disconnects right after its last event: apps such as games can drop input
            // that arrives just before they lose the foreground.
            void releaseX() {
                if (auto w = window.lock())
                    x11::release(w);
            }
            void leaveKeyboard() {
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
            // Lets go of the keys the sender holds. Without a lease the keyboard leaves the window, which releases them
            // for the application. With one the focus stays, so each key goes up.
            void releaseKeyboard() {
                if (!leased())
                    leaveKeyboard();
                auto       s   = enteredKeyboard ? wlSeat() : nullptr;
                const auto now = Time::millis(Time::steadyNow());
                for (auto code : keys) {
                    if (s)
                        for (auto& k : s->m_keyboards)
                            if (k)
                                k->sendKey(now, code, WL_KEYBOARD_KEY_STATE_RELEASED);
                    if (state)
                        xkb_state_update_key(state, code + 8, XKB_KEY_UP);
                }
                // The sender is stopping or gone, so only the window learns the modifiers.
                if (s && state && !keys.empty())
                    mods(s, false);
                keys.clear();
            }
            // Lets go of the buttons the sender holds. Without a lease the pointer leaves the window; with one it stays
            // where it is.
            void releasePointer() {
                if (!leased()) {
                    leavePointer();
                    return;
                }
                auto s = enteredPointer && !buttons.empty() ? wlSeat() : nullptr;
                if (s) {
                    const auto now = Time::millis(Time::steadyNow());
                    for (auto& p : s->m_pointers)
                        if (p) {
                            for (auto button : buttons)
                                p->sendButton(now, button, WL_POINTER_BUTTON_STATE_RELEASED);
                            p->sendFrame();
                        }
                }
                buttons.clear();
            }
            bool leased() const {
                return lease && Time::steadyNow() < lease->until;
            }
            std::optional<SLeaseState> leaseState() const {
                if (!leased())
                    return std::nullopt;
                return SLeaseState{.remainingMs = sc<int>(std::chrono::ceil<std::chrono::milliseconds>(lease->until - Time::steadyNow()).count()), .owner = lease->owner};
            }
            // The emulated focus then ends as without a lease, once input has paused for a second.
            void endLease() {
                lease.reset();
                scheduleIdle();
            }
            bool sharesKeyboardFocus() {
                auto w = window.lock();
                return w && sharesConnection(w, g_pSeatManager->m_state.keyboardFocus);
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
                regions = pointerRegions(w);
                for (const auto& region : regions) {
                    auto r = eis_device_new_region(pointer);
                    eis_region_set_offset(r, region.x, region.y);
                    eis_region_set_size(r, region.width, region.height);
                    eis_region_add(r);
                    eis_region_unref(r);
                }
                eis_device_add(pointer);
                if (!paused)
                    eis_device_resume(pointer);
            }
            // Replaces the pointer device once its regions no longer match the window and its monitor, after a resize or
            // a move. Checked once the current batch of events is handled, since moves are seen inside Hyprland's own
            // updates.
            void checkRegionsLater() {
                if (regionCheck)
                    return;
                regionCheck = wl_event_loop_add_idle(
                    g_pCompositor->m_wlEventLoop,
                    [](void* data) {
                        auto* self        = static_cast<Session*>(data);
                        self->regionCheck = nullptr; // libwayland removes it after this call
                        auto w            = self->window.lock();
                        if (self->pointer && w && pointerRegions(w) != self->regions) {
                            self->clearDevice(self->pointer);
                            self->makePointer();
                        }
                    },
                    this);
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
            void mods(const SP<CWLSeatResource>& s, bool toSender = true) {
                const auto dep   = xkb_state_serialize_mods(state, XKB_STATE_MODS_DEPRESSED);
                const auto lat   = xkb_state_serialize_mods(state, XKB_STATE_MODS_LATCHED);
                const auto lock  = xkb_state_serialize_mods(state, XKB_STATE_MODS_LOCKED);
                const auto group = xkb_state_serialize_layout(state, XKB_STATE_LAYOUT_EFFECTIVE);
                for (auto& k : s->m_keyboards)
                    if (k)
                        k->sendMods(dep, lat, lock, group);
                if (toSender)
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
                // Another socket may address a second window on this same Wayland client. The client's keyboard and
                // pointer are on one surface at a time, so that window loses its emulated focus and its lease.
                for (auto& [_, other] : sessions) {
                    if (other.get() != this && other->wlSeat() == s) {
                        other->lease.reset();
                        other->leave();
                    }
                }
                const bool isKeyboard = device == keyboard;
                // XWayland sends keys to the X focus and pointer events to the topmost X window under the pointer.
                // Clicks and scrolling take X focus too, as a real click does: Wine only takes input once X focus has
                // made it the foreground window. If the X server does not respond, the sender learns that input was
                // dropped.
                const bool takesFocus =
                    isKeyboard || std::ranges::any_of(pending, [&](eis_event* e) {
                        const auto type = eis_event_get_type(e);
                        return eis_event_get_device(e) == device &&
                            ((type == EIS_EVENT_BUTTON_BUTTON && eis_event_button_get_is_press(e)) || type == EIS_EVENT_SCROLL_DELTA || type == EIS_EVENT_SCROLL_DISCRETE);
                    });
                std::optional<x11::SStack> stack;
                if (w->m_isX11 && ((takesFocus && !x11::focus(w)) || (!isKeyboard && !(stack = x11::raise(w))))) {
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
                            // A point over one of the window's popups reaches the popup, also outside the window. Any
                            // other point is clamped to the window.
                            const auto size = w->size(Desktop::View::IGeometric::GEOMETRIC_GOAL);
                            position = popupAt(w, {x, y}) ? Vector2D{x, y} : Vector2D{std::clamp(x, 0.0, std::max(0.0, size.x - 1)), std::clamp(y, 0.0, std::max(0.0, size.y - 1))};
                        }
                        if (type == EIS_EVENT_BUTTON_BUTTON) {
                            const auto button = eis_event_button_get_button(e);
                            const bool down   = eis_event_button_get_is_press(e);
                            // sendLeave already releases buttons during a same-client switch.
                            if (down ? buttons.contains(button) : !buttons.contains(button))
                                continue;
                        }
                        Vector2D local;
                        auto     target = surfaceAt(w, position, local);
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
                        cursor::show(w, position, type == EIS_EVENT_BUTTON_BUTTON && eis_event_button_get_is_press(e));
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
                // X focus goes back at once when the user's keyboard is on the same Wayland client, otherwise once input
                // pauses.
                auto realKeyboard = g_pSeatManager->m_state.keyboardFocus.lock();
                if (takesFocus && realKeyboard && realKeyboard->client() == s->client())
                    x11::release(w);
                lastInput      = Time::steadyNow();
                commitsAtInput = content->commits;
                wl_event_source_timer_update(timer, 16);
            }
            int tick() {
                auto w = window.lock();
                if (!w)
                    return 0;
                maybeResume();
                resumeHeld();
                const auto now  = Time::steadyNow();
                const auto held = leaseState();
                if (!held)
                    lease.reset();
                const bool recent = std::chrono::duration_cast<std::chrono::milliseconds>(now - lastInput).count() < 1000;
                if (!recent && !held && keys.empty() && buttons.empty()) {
                    leave();
                    releaseX();
                }
                // When a send to a sender fails, as it does once the sender has exited, libeis closes the connection
                // without a disconnect event, and what the sender held stays pressed. A failed ping it does report, so a
                // sender that has sent nothing for a second gets one every second.
                if (client && !recent && now - lastPing >= std::chrono::seconds(1)) {
                    lastPing = now;
                    if (auto* ping = eis_client_new_ping(client)) {
                        eis_ping(ping);
                        eis_ping_unref(ping);
                    }
                }
                // Hidden windows do not get compositor frame callbacks. They get them for a second after input, and while
                // a sender has its devices, which it keeps while it pauses between inputs and closes when it exits, also
                // when libeis does not report the disconnect.
                if (recent || (client && (pointer || keyboard))) {
                    content->sendFrames();
                    wl_event_source_timer_update(timer, 16);
                } else if (held || client) {
                    // A lease keeps the emulated focus until it ends, and a sender gets its pings.
                    const int delay = held ? std::max(1, held->remainingMs) : 1000;
                    wl_event_source_timer_update(timer, client ? std::min(delay, 1000) : delay);
                }
                return 0;
            }
            int poll() {
                eis_dispatch(context);
                process();
                return 0;
            }
            // The window is gone, so what the sender still has in the socket can only be dropped. Taking it answers the
            // pings among it, so a sender whose input closed the window, such as exit in a shell, learns that the plugin
            // processed everything before them instead of losing the answers with the connection. A ping after dropped
            // input must not confirm it, so the devices are removed first: senders take no answer after that as a
            // confirmation.
            void answerPings() {
                eis_dispatch(context);
                if (stashed)
                    eis_event_unref(std::exchange(stashed, nullptr));
                bool removed       = false;
                auto removeDevices = [&] {
                    if (std::exchange(removed, true))
                        return;
                    clearDevice(pointer);
                    clearDevice(keyboard);
                };
                if (!pending.empty())
                    removeDevices();
                while (auto* e = eis_get_event(context)) {
                    if (carriesInput(e) && (eis_event_get_device(e) == pointer || eis_event_get_device(e) == keyboard))
                        removeDevices();
                    eis_event_unref(e);
                }
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
                                    releaseKeyboard();
                                if (d == pointer)
                                    releasePointer();
                                drop(d);
                                scheduleIdle();
                            }
                            break;
                        default: break;
                    }
                    eis_event_unref(e);
                }
            }
        };

        void closeClosedWindow(const std::string& address) {
            if (const auto it = sessions.find(address); it != sessions.end()) {
                it->second->answerPings();
                sessions.erase(it);
            }
        }

        Session* sessionOf(const PHLWINDOW& window) {
            for (const auto& [_, session] : sessions)
                if (session->window.lock() == window)
                    return session.get();
            return nullptr;
        }

        bool x11Window(const PHLWINDOW& window, Session* session) {
            return session ? session->xServer() != nullptr : x11::server(window) != nullptr;
        }

        bool protectedSurface(const SP<CWLSurfaceResource>& surface) {
            for (auto& [_, session] : sessions) {
                auto w = session->window.lock();
                if (!w || w == Desktop::focusState()->window())
                    continue;
                bool found = false;
                forEachSurface(w, [&](SP<CWLSurfaceResource> s) { found |= s == surface; });
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

        // Every change to a window's position or size updates its decorations, the one place where moves show.
        void updateDecos(Desktop::View::CWindow* self) {
            using Fn = void (*)(Desktop::View::CWindow*);
            reinterpret_cast<Fn>(originalUpdateDecos)(self);
            for (auto& [_, session] : sessions) {
                if (session->window.lock().get() == self)
                    session->checkRegionsLater();
            }
        }

        // When the real focus changes, Hyprland sends every keyboard or pointer a leave, which does nothing for one
        // without a surface. A lease keeps the emulated focus of a window on another connection than the new focus, so
        // its keyboards or pointers pass as unfocused while this lives, and get their surface back afterwards.
        template <typename Resource>
        class CKeptFocus {
          public:
            CKeptFocus(const SP<CWLSurfaceResource>& next, WP<CWLSurfaceResource> Session::* entered, std::vector<WP<Resource>> CWLSeatResource::* resources,
                       bool Session::* keeping) : m_keeping(keeping) {
                for (auto& [_, session] : sessions) {
                    auto surface = (session.get()->*entered).lock();
                    auto seat    = surface && session->leased() ? session->wlSeat() : nullptr;
                    if (!seat || (next && next->client() == surface->client()))
                        continue;
                    const auto before = m_hidden.size();
                    for (auto& resource : (*seat).*resources) {
                        if (resource && surfaceOf(*resource) == surface)
                            m_hidden.emplace_back(resource, std::exchange(surfaceOf(*resource), {}));
                    }
                    if (m_hidden.size() > before) {
                        session.get()->*keeping = true;
                        m_kept.push_back(session.get());
                    }
                }
            }
            ~CKeptFocus() {
                for (auto& [resource, surface] : m_hidden) {
                    if (resource && !surfaceOf(*resource))
                        surfaceOf(*resource) = surface;
                }
                // Only sessions that still exist are touched.
                for (auto& [_, session] : sessions) {
                    if (std::ranges::contains(m_kept, session.get()))
                        session.get()->*m_keeping = false;
                }
            }
            CKeptFocus(const CKeptFocus&)            = delete;
            CKeptFocus& operator=(const CKeptFocus&) = delete;

          private:
            std::vector<std::pair<WP<Resource>, WP<CWLSurfaceResource>>> m_hidden;
            std::vector<Session*>                                        m_kept;
            bool Session::* m_keeping;
        };
        void setKeyboardFocus(CSeatManager* self, SP<CWLSurfaceResource> surface) {
            CKeptFocus<CWLKeyboardResource> kept(surface, &Session::enteredKeyboard, &CWLSeatResource::m_keyboards, &Session::keepingKeyboard);
            using Fn = void (*)(CSeatManager*, SP<CWLSurfaceResource>);
            reinterpret_cast<Fn>(originalKeyboardFocus)(self, surface);
        }
        void setPointerFocus(CSeatManager* self, SP<CWLSurfaceResource> surface, const Vector2D& local) {
            CKeptFocus<CWLPointerResource> kept(surface, &Session::enteredPointer, &CWLSeatResource::m_pointers, &Session::keepingPointer);
            using Fn = void (*)(CSeatManager*, SP<CWLSurfaceResource>, const Vector2D&);
            reinterpret_cast<Fn>(originalPointerFocus)(self, surface, local);
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

        // The instance's directory under $XDG_RUNTIME_DIR/hyprmasq: its signature without the commit hash in front, which
        // leaves the time and random number that tell running instances apart. With the hash, socket paths would outgrow
        // sun_path with an XDG_RUNTIME_DIR of more than 16 characters. hyprmasq-mcp names its directory the same way.
        std::string instanceDirectory() {
            const auto& signature = g_pCompositor->m_instanceSignature;
            const auto  separator = signature.find('_');
            return separator == std::string::npos || separator + 1 == signature.size() ? signature : signature.substr(separator + 1);
        }
    }

    void initEis(void* handle) {
        socketNonce           = std::random_device{}();
        originalPopupGrab     = hook(handle, "CXDGShellProtocol::addOrStartGrab", reinterpret_cast<void*>(&popupGrab));
        originalStartDrag     = hook(handle, "CWLDataDeviceProtocol::initiateDrag", reinterpret_cast<void*>(&startDrag));
        originalActivateX11   = hook(handle, "Desktop::View::CWindow::activateX11", reinterpret_cast<void*>(&activateX11));
        originalWindowFocus   = hook(handle, "Desktop::CFocusState::rawWindowFocus", reinterpret_cast<void*>(&windowFocus));
        originalActivate      = hook(handle, "Desktop::View::CWindow::activate", reinterpret_cast<void*>(&activate));
        originalUpdateDecos   = hook(handle, "Desktop::View::CWindow::updateWindowDecos", reinterpret_cast<void*>(&updateDecos));
        originalKeyboardFocus = hook(handle, "CSeatManager::setKeyboardFocus", reinterpret_cast<void*>(&setKeyboardFocus));
        originalPointerFocus  = hook(handle, "CSeatManager::setPointerFocus", reinterpret_cast<void*>(&setPointerFocus));
        monitorLayout         = Event::bus()->m_events.monitor.layoutChanged.listen([] {
            for (auto& [_, session] : sessions)
                session->checkRegionsLater();
        });
    }
    void shutdownEis() {
        monitorLayout.reset();
        sessions.clear();
    }
    std::expected<SEisInfo, SError> openEis(const PHLWINDOW& w, bool force) {
        const auto address = windowAddress(w);
        if (sessions.contains(address)) {
            auto& session = *sessions.at(address);
            session.force = force;
            session.maybeResume();
            return SEisInfo{.address = address, .path = session.path, .id = session.id, .force = force};
        }
        auto kb = g_pSeatManager->m_keyboard.lock();
        if (!kb || !kb->m_xkbKeymap || !kb->m_xkbKeymapFD.isValid())
            return std::unexpected(SError{eErrorCode::INTERNAL, "an active Wayland seat keyboard with a keymap is required"});
        const char* runtime = getenv("XDG_RUNTIME_DIR");
        if (!runtime || runtime[0] != '/')
            return std::unexpected(SError{eErrorCode::INTERNAL, "XDG_RUNTIME_DIR must be absolute"});
        const auto directory = instanceDirectory();
        auto       path      = std::format("{}/hyprmasq/{}/{}.eis", runtime, directory, address);
        // A socket's path has to fit sun_path with its terminating zero.
        if (path.size() >= sizeof(sockaddr_un::sun_path))
            return std::unexpected(SError{eErrorCode::INTERNAL,
                                          std::format("the socket path {} has {} bytes, more than the {} of a Unix socket path; use a shorter XDG_RUNTIME_DIR", path, path.size(),
                                                      sizeof(sockaddr_un::sun_path) - 1)});
        int base     = open(runtime, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        int root     = base < 0 ? -1 : privateDir(base, "hyprmasq");
        int instance = root < 0 ? -1 : privateDir(root, directory.c_str());
        if (base >= 0)
            close(base);
        if (root >= 0)
            close(root);
        if (instance < 0)
            return std::unexpected(SError{eErrorCode::INTERNAL, "cannot create private hyprmasq socket directory"});
        close(instance);
        auto  session = std::make_unique<Session>(w, std::move(path), force);
        auto* s       = session.get();
        s->context    = eis_new(nullptr);
        if (!s->context || eis_setup_backend_socket(s->context, s->path.c_str()) != 0)
            return std::unexpected(SError{eErrorCode::INTERNAL, "cannot create EIS socket " + s->path});
        // Enforce permissions on the pathname too, independently of libeis or the process umask.
        if (chmod(s->path.c_str(), 0600) != 0)
            return std::unexpected(SError{eErrorCode::INTERNAL, "cannot restrict EIS socket permissions"});
        s->fdSource = wl_event_loop_add_fd(
            g_pCompositor->m_wlEventLoop, eis_get_fd(s->context), WL_EVENT_READABLE, [](int, uint32_t, void* data) { return static_cast<Session*>(data)->poll(); }, s);
        s->timer = wl_event_loop_add_timer(g_pCompositor->m_wlEventLoop, [](void* data) { return static_cast<Session*>(data)->tick(); }, s);
        if (!s->fdSource || !s->timer)
            return std::unexpected(SError{eErrorCode::INTERNAL, "cannot register EIS event sources"});
        s->unmap   = w->m_events.unmap.listen([address] { closeClosedWindow(address); });
        s->destroy = w->m_events.destroy.listen([address] { closeClosedWindow(address); });
        s->resize  = w->m_events.resize.listen([s] { s->checkRegionsLater(); });
        // Hyprland sends every keyboard and pointer a leave on a change of the real focus, unless a lease kept it.
        s->keyboardFocus = g_pSeatManager->m_events.keyboardFocusChange.listen([s] {
            if (!s->keepingKeyboard)
                s->enteredKeyboard.reset();
            // The user's keys now go to the window's connection.
            if (s->lease && s->sharesKeyboardFocus())
                s->endLease();
            s->maybeResume();
        });
        s->pointerFocus  = g_pSeatManager->m_events.pointerFocusChange.listen([s] {
            if (!s->keepingPointer)
                s->enteredPointer.reset();
        });
        SEisInfo info{.address = address, .path = s->path, .id = s->id, .force = force};
        sessions.emplace(address, std::move(session));
        return info;
    }
    bool closeEis(const std::string& address) {
        return sessions.erase(address) != 0;
    }
    std::optional<SSocketState> socketState(const PHLWINDOW& window) {
        auto* session = sessionOf(window);
        if (!session)
            return std::nullopt;
        return SSocketState{.path             = session->path,
                            .id               = session->id,
                            .force            = session->force,
                            .lastInput        = session->lastInput,
                            .framesSinceInput = sc<int>(session->content->commits - session->commitsAtInput),
                            .content          = session->content,
                            .lease            = session->leaseState()};
    }
    SCapabilities capabilities(const PHLWINDOW& window) {
        auto* session = sessionOf(window);
        // Input to a window on the connection of the real focus hands the focus back after every frame, which releases
        // whatever it held. For buttons, that is the connection of the real pointer focus.
        const bool keyboardShared = sharesConnection(window, g_pSeatManager->m_state.keyboardFocus);
        const bool pointerShared  = sharesConnection(window, g_pSeatManager->m_state.pointerFocus);
        const bool isX11          = x11Window(window, session);
        return {.lease    = !isX11 && !keyboardShared,
                .hold     = !keyboardShared && !pointerShared,
                .dnd      = window == Desktop::focusState()->window(),
                .x11Focus = isX11 && !(session && session->force)};
    }
    std::expected<SLeaseState, SError> holdLease(const PHLWINDOW& window, int ms, const std::string& owner) {
        auto* session = sessionOf(window);
        if (!session)
            return std::unexpected(SError{eErrorCode::NO_SOCKET, "no open socket for " + windowAddress(window)});
        if (x11Window(window, session))
            return std::unexpected(SError{eErrorCode::UNSUPPORTED, "an X11 window cannot hold a lease: holding X focus would take the user's typing in their X11 apps"});
        if (sharesConnection(window, g_pSeatManager->m_state.keyboardFocus))
            return std::unexpected(SError{eErrorCode::UNSUPPORTED, "the window shares its Wayland connection with the window that has the keyboard focus"});
        session->lease = SLease{.until = Time::steadyNow() + std::chrono::milliseconds(ms), .owner = owner};
        // tick() keeps the focus until the lease ends.
        session->scheduleIdle();
        return *session->leaseState();
    }
    bool releaseLease(const PHLWINDOW& window, const std::optional<std::string>& owner) {
        auto* session = sessionOf(window);
        if (!session || !session->leased() || (owner && *owner != session->lease->owner))
            return false;
        session->endLease();
        return true;
    }
    bool leaseKeepsPointer(const PHLWINDOW& window) {
        auto* session = sessionOf(window);
        return session && session->leased() && !session->enteredPointer.expired();
    }
    std::vector<SEisInfo> listEis() {
        std::vector<SEisInfo> result;
        for (const auto& [address, session] : sessions)
            result.push_back({.address = address, .path = session->path, .id = session->id, .force = session->force});
        return result;
    }
}
