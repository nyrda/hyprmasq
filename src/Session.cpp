#include "Session.hpp"
#include "Cursor.hpp"
#include "Placement.hpp"
#include "PrivateAccess.hpp"
#include "Selection.hpp"
#include "Wait.hpp"
#include "X11.hpp"
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/desktop/state/FocusState.hpp>
#include <hyprland/src/desktop/view/Popup.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/devices/IKeyboard.hpp>
#include <hyprland/src/managers/SeatManager.hpp>
#include <hyprland/src/managers/input/InputManager.hpp>
#include <hyprland/src/output/Monitor.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#include <hyprland/src/protocols/core/DataDevice.hpp>
#include <hyprland/src/protocols/core/Seat.hpp>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <ranges>
#include <utility>

namespace hyprmasq::input {
    namespace {
        // How long a window of the connection with the real keyboard focus keeps the emulated focus after a command key,
        // see SSession::keepKeyboard. Chromium answers a shortcut 10 to 90 ms after the key on a hidden workspace.
        constexpr auto KEYS_SETTLE = std::chrono::milliseconds(250);
        // How long a frame waits for the user to let go of the buttons or keys that it would release, see
        // SSession::userHolds, before it is dropped with a pause.
        constexpr auto USER_HOLD_WAIT = std::chrono::milliseconds(1000);
        // How often a session's timer runs while it has input or frame callbacks to send: once a frame at 60 Hz.
        constexpr int TICK_MS = 16;
        // How long paused devices stay paused before they may resume, so that a sender notices the pause, see
        // SSession::maybeResume.
        constexpr int PAUSE_GRACE_MS = 500;
        // How long input waits for xwayland-satellite to handle the batch of Wayland events before it, see
        // SSession::hold.
        constexpr auto SATELLITE_BATCH = std::chrono::milliseconds(50);
        // How long after input a window keeps the emulated focus and gets frame callbacks.
        constexpr int IDLE_MS = 1000;
        // How often a sender that has sent nothing gets a ping, which tells whether it is still there.
        constexpr int PING_INTERVAL_MS = 1000;
        // The most events of unfinished frames a session keeps. A sender that sends more is disconnected.
        constexpr size_t MAX_PENDING = 4096;
        // The most presses whose serials a session keeps, see SSession::rememberPress.
        constexpr size_t MAX_PRESS_SERIALS = 8;

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

        // The popup at a window-local point. Hyprland places popups at the window's animated position when it tests for
        // them, so the point is given there too, and a window that moves finds its popups where shots show them. Shots
        // show a window's popups where the renderer draws them: less the window's geometry offset, which Hyprland's popup
        // hit testing leaves out. Points are looked up in the frame of the shots, so that input reaches what they show
        // there. The offset of the window's own surface is 0 in both.
        SP<Desktop::View::CPopup> popupAt(const PHLWINDOW& w, const Vector2D& point) {
            if (w->m_isX11 || !w->m_popupHead || w->m_popupHead->popupTreeCount() == 0)
                return nullptr;
            const auto popup = w->m_popupHead->at(w->position(Desktop::View::IGeometric::GEOMETRIC_CURRENT) + geometryOffset(w) + point);
            return popup && popup->wlSurface() && popup->wlSurface()->resource() ? popup : nullptr;
        }

        // The last coordinate of a window's extent that still arrives inside it. Wayland sends pointer positions in
        // wl_fixed, rounded to the nearest 1/256 logical pixel, so a point closer to the edge would arrive on it.
        double lastInside(double extent) {
            return std::max(0.0, (std::ceil(extent * 256) - 1) / 256);
        }

        // The surface at a window-local point, and the point in that surface's coordinates.
        SP<CWLSurfaceResource> surfaceAt(const PHLWINDOW& w, const Vector2D& point, Vector2D& local) {
            // Like Hyprland, address X11 windows through their one surface, in XWayland's scale.
            if (w->m_isX11) {
                local = point * w->m_X11SurfaceScaledBy;
                return w->resource();
            }
            if (const auto popup = popupAt(w, point)) {
                local = geometryOffset(w) + point - popup->coordsRelativeToParent();
                return popup->wlSurface()->resource();
            }
            const auto surface = w->resource();
            if (!surface)
                return nullptr;
            const auto [found, at] = surface->at(point, true);
            local                  = at;
            return found;
        }

        // Keys whose effect an app may apply only after a round trip, as Chromium runs a shortcut once the page has let its
        // key go by: keys pressed with Ctrl, Alt or Super held, function keys, Escape and media keys. The state is the
        // one before the key.
        bool commandKey(xkb_state* state, uint32_t code) {
            if (xkb_state_mod_names_are_active(state, XKB_STATE_MODS_EFFECTIVE, XKB_STATE_MATCH_ANY, XKB_MOD_NAME_CTRL, XKB_MOD_NAME_ALT, XKB_MOD_NAME_LOGO, nullptr) > 0)
                return true;
            const auto sym = xkb_state_key_get_one_sym(state, code + 8);
            return (sym >= XKB_KEY_F1 && sym <= XKB_KEY_F35) || sym == XKB_KEY_Escape || (sym >= 0x1008FF00 && sym <= 0x1008FFFF);
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

        // Sends a scroll event to the pointer resource.
        void sendScroll(const WP<CWLPointerResource>& p, eis_event* e, uint64_t now) {
            const auto   type     = eis_event_get_type(e);
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
                if (type == EIS_EVENT_SCROLL_STOP && (axis == WL_POINTER_AXIS_HORIZONTAL_SCROLL ? eis_event_scroll_get_stop_x(e) : eis_event_scroll_get_stop_y(e)))
                    p->sendAxisStop(now, axis);
            }
        }
    }

    SSession::SSession(const PHLWINDOW& w, std::string p, std::string i, bool f) :
        window(w), path(std::move(p)), id(std::move(i)), content(std::make_shared<wait::CContentWatch>(w)), force(f) {}

    SSession::~SSession() {
        // A drag that was dropped waits for the destination without the socket.
        cancelDrag(closing);
        lease.reset(); // the emulated focus ends with the socket
        returnKeyboard(false);
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

    void SSession::clearDevice(eis_device*& d) {
        if (!d)
            return;
        drop(d);
        if (d == deferred)
            deferred = nullptr;
        if (d == pointer)
            releasePointer();
        if (d == keyboard) {
            keyboardEmulating = false;
            releaseKeyboard();
            if (state)
                xkb_state_unref(state);
            state = nullptr;
        }
        eis_device_remove(d);
        eis_device_unref(d);
        d = nullptr;
    }

    void SSession::clearClient() {
        cancelDrag("sender_gone");
        pressSerials.clear();
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
        stashed  = nullptr;
        deferred = nullptr;
        deferredSince.reset();
        scheduleIdle();
    }

    void SSession::scheduleIdle() {
        if (timer)
            wl_event_source_timer_update(timer, TICK_MS);
    }

    wl_client* SSession::xServer() {
        if (!serverKnown) {
            auto w = window.lock();
            if (!w || !w->resource())
                return nullptr;
            server      = x11::server(w);
            serverKnown = true;
        }
        return server;
    }

    SP<CWLSeatResource> SSession::wlSeat() {
        auto w = window.lock();
        auto s = w ? w->resource() : nullptr;
        return s ? g_pSeatManager->seatResourceForClient(s->client()) : nullptr;
    }

    void SSession::releaseX() {
        if (auto w = window.lock())
            x11::release(w);
    }

    void SSession::leaveKeyboard() {
        // The connection's keyboard focus goes back to the user's window, not nowhere.
        if (keyboardKept) {
            returnKeyboard(false);
            return;
        }
        auto s = wlSeat();
        if (s && enteredKeyboard)
            for (auto& k : s->m_keyboards)
                if (k)
                    k->sendLeave();
        enteredKeyboard.reset();
    }

    void SSession::leavePointer() {
        pressSerials.clear();
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

    void SSession::leave() {
        leaveKeyboard();
        leavePointer();
    }

    void SSession::dragCancelled(std::string_view reason) {
        ++drags->id;
        ++drags->cancelled;
        drags->phase     = "cancelled";
        drags->privately = false;
        drags->reason    = reason;
        drags->endedAt   = Time::steadyNow();
        drags->end(drags->id, "cancelled", reason);
        drags->changed.emit();
    }

    std::shared_ptr<drag::CPrivateDrag> SSession::activeDrag() const {
        auto d = drag.lock();
        return d && d->active() ? d : nullptr;
    }

    void SSession::cancelDrag(std::string_view reason) {
        if (auto d = activeDrag())
            d->cancel(reason);
    }

    std::optional<std::string_view> SSession::dragRefusal(const drag::SRequest* request, const SP<CWLDataSourceResource>& source, const SP<CWLSurfaceResource>& icon) {
        auto w = window.lock();
        auto s = wlSeat();
        if (!w || !s || (!source && !(request && request->sourceless)))
            return "source_gone";
        if (w->m_isX11 || xServer())
            return "x11";
        // The user's input to the connection would reach the drag's client in the middle of it: their keys, their
        // pointer, or a finger they hold down.
        if (sharesConnection(w, g_pSeatManager->m_state.keyboardFocus) || sharesConnection(w, g_pSeatManager->m_state.pointerFocus) ||
            sharesConnection(w, g_pSeatManager->m_state.touchFocus))
            return "shared_focus";
        if (PROTO::data->dndActive())
            return "user_drag";
        const auto press = request ? std::ranges::find(pressSerials, request->serial, &SPress::serial) : pressSerials.end();
        if (!request || !request->device || request->device->client() != s->client() || press == pressSerials.end() || !buttons.contains(press->button))
            return "serial";
        if (request->deviceVersion < 3 || (!request->sourceless && request->sourceVersion < 3))
            return "version";
        if (icon && icon->m_role->role() != SURFACE_ROLE_UNASSIGNED)
            return "icon";
        // A drop that waits for its destination does not hold up the next drag: waits follow both.
        if (drag::activeOn(s->client()))
            return "busy";
        if (drag::full(s->client()))
            return "limit";
        return std::nullopt;
    }

    void SSession::beginDrag(const drag::SRequest& request, const SP<CWLDataSourceResource>& source, const SP<CWLSurfaceResource>& icon) {
        auto w     = window.lock();
        auto s     = wlSeat();
        auto d     = w && s ? drag::start(request, source, icon, drags) : nullptr;
        auto press = std::ranges::find(pressSerials, request.serial, &SPress::serial);
        if (!d || press == pressSerials.end()) {
            if (source)
                source->cancelled();
            dragCancelled("device_gone");
            return;
        }
        drag       = d;
        dragButton = press->button;
        pressSerials.clear();
        // The emulated pointer leaves the window, as a pointer leaves the surface where a drag starts, but without
        // the release that Hyprland's sendLeave sends outside its own drags: the button is the drag's.
        for (auto& p : s->m_pointers)
            if (p && surfaceOf(*p)) {
                pressedOf(*p).clear();
                p->sendLeave();
                p->sendFrame();
            }
        enteredPointer.reset();
        dragMotion(d, w);
    }

    void SSession::dragMotion(const std::shared_ptr<drag::CPrivateDrag>& d, const PHLWINDOW& w) {
        const auto             size = w->size(Desktop::View::IGeometric::GEOMETRIC_GOAL);
        SP<CWLSurfaceResource> target;
        Vector2D               local;
        if (popupAt(w, pointerAt) || (pointerAt.x >= 0 && pointerAt.y >= 0 && pointerAt.x < size.x && pointerAt.y < size.y))
            target = surfaceAt(w, pointerAt, local);
        d->motion(target, local);
    }

    void SSession::releaseKeyboard() {
        if (!leased() && !keyboardKept)
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

    void SSession::releasePointer() {
        cancelDrag("sender_gone");
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

    bool SSession::leased() const {
        return lease && Time::steadyNow() < lease->until;
    }

    std::optional<SLeaseState> SSession::leaseState() const {
        if (!leased())
            return std::nullopt;
        return SLeaseState{.remainingMs = sc<int>(std::chrono::ceil<std::chrono::milliseconds>(lease->until - Time::steadyNow()).count()), .owner = lease->owner};
    }

    void SSession::endLease() {
        lease.reset();
        scheduleIdle();
    }

    bool SSession::sharesKeyboardFocus() {
        auto w = window.lock();
        return w && sharesConnection(w, g_pSeatManager->m_state.keyboardFocus);
    }

    void SSession::makePointer() {
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

    void SSession::checkRegionsLater() {
        if (regionCheck)
            return;
        regionCheck = wl_event_loop_add_idle(
            g_pCompositor->m_wlEventLoop,
            [](void* data) {
                auto* self        = static_cast<SSession*>(data);
                self->regionCheck = nullptr; // libwayland removes it after this call
                auto w            = self->window.lock();
                if (self->pointer && w && pointerRegions(w) != self->regions) {
                    self->cancelDrag("window_moved");
                    self->clearDevice(self->pointer);
                    self->makePointer();
                }
            },
            this);
    }

    void SSession::makeKeyboard() {
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

    bool SSession::blocked() {
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

    void SSession::setPaused(bool p) {
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
            wl_event_source_timer_update(timer, PAUSE_GRACE_MS);
    }

    void SSession::pause(const char* reason) {
        lastPause = reason;
        setPaused(true);
    }

    void SSession::maybeResume() {
        if (!paused || blocked())
            return;
        const auto waited = std::chrono::duration_cast<std::chrono::milliseconds>(Time::steadyNow() - pausedAt).count();
        if (waited >= PAUSE_GRACE_MS)
            setPaused(false);
        else
            wl_event_source_timer_update(timer, PAUSE_GRACE_MS - waited);
    }

    void SSession::drop(eis_device* device) {
        std::erase_if(pending, [&](eis_event* e) {
            if (eis_event_get_device(e) != device)
                return false;
            eis_event_unref(e);
            return true;
        });
    }

    void SSession::hold(std::optional<Time::steady_tp> until) {
        if (auto w = window.lock(); w && w->resource())
            wl_client_flush(w->resource()->client());
        holdUntil = until.value_or(Time::steadyNow() + SATELLITE_BATCH);
        lastInput = Time::steadyNow();
        if (content)
            content->lastInput = lastInput;
        wl_event_source_timer_update(timer, TICK_MS);
    }

    bool SSession::leavesSatelliteTooSoon(eis_event* e) {
        auto       w      = window.lock();
        const auto type   = eis_event_get_type(e);
        const bool leaves = (eis_event_get_device(e) == keyboard && (type == EIS_EVENT_DEVICE_STOP_EMULATING || type == EIS_EVENT_DEVICE_CLOSED)) ||
            (type == EIS_EVENT_CLIENT_DISCONNECT && eis_event_get_client(e) == client);
        return leaves && enteredKeyboard && w && !w->m_isX11 && xServer() && Time::steadyNow() < lastKeys + SATELLITE_BATCH;
    }

    bool SSession::holding() const {
        return deferred || Time::steadyNow() < holdUntil;
    }

    void SSession::resumeHeld() {
        if (Time::steadyNow() < holdUntil)
            return;
        if (auto* d = std::exchange(deferred, nullptr))
            deliver(d);
        else if (deferredKeys) {
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

    void SSession::enterKeyboard(const SP<CWLSeatResource>& s, const SP<CWLSurfaceResource>& target, bool restore) {
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

    void SSession::keepKeyboard(const SP<CWLSeatResource>& s) {
        keyboardKept = true;
        keptSeat     = s;
        keysSettled  = Time::steadyNow() + KEYS_SETTLE;
    }

    void SSession::returnKeyboard(bool byUser) {
        if (!std::exchange(keyboardKept, false))
            return;
        const auto s    = keptSeat.lock();
        const auto real = g_pSeatManager->m_state.keyboardFocus.lock();
        keptSeat.reset();
        if (byUser && Time::steadyNow() < lastCommand + KEYS_SETTLE)
            ++interrupted;
        if (s && real && real->client() == s->client())
            restoreKeyboard(s, real);
        else
            enteredKeyboard.reset();
    }

    void SSession::restoreKeyboard(const SP<CWLSeatResource>& s, const SP<CWLSurfaceResource>& real) {
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
        selection::restore(s->client());
    }

    void SSession::mods(const SP<CWLSeatResource>& s, bool toSender) {
        // A sender without a keyboard holds no keys.
        if (!state) {
            for (auto& k : s->m_keyboards)
                if (k)
                    k->sendMods(0, 0, 0, 0);
            return;
        }
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

    void SSession::rememberPress(uint32_t serial, uint32_t button) {
        if (pressSerials.size() >= MAX_PRESS_SERIALS)
            pressSerials.erase(pressSerials.begin());
        pressSerials.push_back({.serial = serial, .button = button});
    }

    const char* SSession::userHolds(eis_device* device, const SP<CWLSeatResource>& s) {
        if (device == keyboard) {
            // A keyboard that the window already has sends the user's window nothing.
            const auto real = g_pSeatManager->m_state.keyboardFocus.lock();
            if (enteredKeyboard || !real || real->client() != s->client() ||
                std::ranges::none_of(g_pInputManager->m_keyboards, [](const SP<IKeyboard>& k) { return k && k->m_enabled && k->m_allowed && !keysOf(*k).empty(); }))
                return nullptr;
            return "user_key";
        }
        const auto real = g_pSeatManager->m_state.pointerFocus.lock();
        if (!real || real->client() != s->client())
            return nullptr;
        for (auto& p : s->m_pointers)
            if (p && surfaceOf(*p) == real && !pressedOf(*p).empty())
                return "user_button";
        return nullptr;
    }

    void SSession::deliver(eis_device* device) {
        // What the frame waits for the X server, over all its round trips.
        x11::CWaitBudget budget;
        auto             w = window.lock();
        auto             s = wlSeat();
        if (!w || !s) {
            drop(device);
            return;
        }
        if (std::ranges::none_of(pending, [device](eis_event* e) { return eis_event_get_device(e) == device; }))
            return;
        // libeis may have queued frames before the sender saw the pause. The sender counts none of them as
        // delivered, so they are dropped.
        if (paused) {
            drop(device);
            return;
        }
        if (blocked()) {
            drop(device);
            pause("x11_focus");
            return;
        }
        if (waitsForUser(device, s))
            return;
        takeConnection(s);
        const bool isKeyboard = device == keyboard;
        const auto has        = [&](auto predicate) { return std::ranges::any_of(pending, [&](eis_event* e) { return eis_event_get_device(e) == device && predicate(e); }); };
        const bool presses    = !isKeyboard && has([](eis_event* e) { return eis_event_get_type(e) == EIS_EVENT_BUTTON_BUTTON && eis_event_button_get_is_press(e); });
        const bool scrolls = !isKeyboard && has([](eis_event* e) { return eis_event_get_type(e) == EIS_EVENT_SCROLL_DELTA || eis_event_get_type(e) == EIS_EVENT_SCROLL_DISCRETE; });
        // XWayland sends keys to the X focus and pointer events to the topmost X window under the pointer.
        // Clicks and scrolling take X focus too, as a real click does: Wine only takes input once X focus has
        // made it the foreground window. If the X server does not respond, the sender learns that input was
        // dropped.
        const bool takesFocus = isKeyboard || presses || scrolls;
        // A press can start a drag, whose request then tells its serial.
        if (presses)
            drag::watchDevices(s->client());
        // What the app copies or selects now is the agent's, and keys and presses, which can paste, as a click on
        // a menu's Paste does, find the agent's selections there, before the enters they bring.
        selection::emulatedInput(s->client(), isKeyboard || presses, presses);
        // A drag that ended without the release of its button leaves the release nothing to do.
        if (!isKeyboard && dragButton && !activeDrag()) {
            buttons.erase(std::exchange(dragButton, 0));
        }

        std::optional<x11::SStack> stack;
        if (w->m_isX11 && ((takesFocus && !x11::focus(w)) || (!isKeyboard && !(stack = x11::raise(w))))) {
            drop(device);
            pause("x11_unresponsive");
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
        SFrame frame{.window = w, .seat = s, .time = Time::millis(Time::steadyNow()), .restore = restore};
        for (auto* e : pending) {
            if (eis_event_get_device(e) != device)
                continue;
            if (isKeyboard)
                deliverKey(frame, e);
            else
                deliverPointer(frame, e);
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
            else if (isKeyboard && !w->m_isX11 && real != w->resource() && (!keys.empty() || Time::steadyNow() < lastCommand + KEYS_SETTLE))
                keepKeyboard(s);
            else if (isKeyboard) {
                keyboardKept = false;
                keptSeat.reset();
                restoreKeyboard(s, real);
            } else
                restorePointer(s, real);
        }
        // The user's window on the connection gets the user's clipboard back after a press, which kept the
        // keyboard focus there.
        if (!isKeyboard && presses && sharesKeyboardFocus())
            selection::restore(s->client());
        if (stack)
            x11::lower(*stack);
        // xwayland-satellite moves X focus once it has handled the batch that entered the window, so keys after
        // the click wait for a later batch, as after an enter for keys.
        if (frame.focusedKeyboard && !w->m_isX11 && xServer())
            hold();
        // X focus goes back at once when the user's keyboard is on the same Wayland client, otherwise once input
        // pauses.
        auto realKeyboard = g_pSeatManager->m_state.keyboardFocus.lock();
        if (takesFocus && realKeyboard && realKeyboard->client() == s->client())
            x11::release(w);
        lastInput          = Time::steadyNow();
        commitsAtInput     = content->commits;
        content->lastInput = lastInput;
        placement::emulatedInput(w, frame.trigger);
        // Input that comes more often than every TICK_MS, as a drag's motion does, keeps putting tick() off, so
        // the frame callbacks that a hidden window needs to go on, such as Firefox before it starts a drag, go out
        // here too, at most every 16 ms, see CContentWatch::sendFrames.
        if (!awake)
            awake.emplace(w);
        content->sendFrames();
        wl_event_source_timer_update(timer, TICK_MS);
    }

    bool SSession::waitsForUser(eis_device* device, const SP<CWLSeatResource>& s) {
        if (const char* held = userHolds(device, s)) {
            const auto now = Time::steadyNow();
            if (!deferredSince)
                deferredSince = now;
            if (now - *deferredSince < USER_HOLD_WAIT) {
                deferred = device;
                wl_event_source_timer_update(timer, TICK_MS);
                return true;
            }
            deferredSince.reset();
            drop(device);
            pause(held);
            return true;
        }
        deferredSince.reset();
        return false;
    }

    void SSession::takeConnection(const SP<CWLSeatResource>& s) {
        for (auto& [_, other] : sessions) {
            if (other.get() != this && other->wlSeat() == s) {
                other->cancelDrag("other_window");
                other->lease.reset();
                other->leave();
            }
        }
    }

    void SSession::deliverKey(SFrame& frame, eis_event* e) {
        const auto& w   = frame.window;
        const auto& s   = frame.seat;
        const auto  now = frame.time;
        if (!enteredKeyboard)
            enterKeyboard(s, w->resource(), frame.restore);
        uint32_t code = eis_event_keyboard_get_key(e);
        bool     down = eis_event_keyboard_get_key_is_press(e);
        if (down ? !keys.insert(code).second : !keys.erase(code))
            return;
        if (down && commandKey(state, code))
            lastCommand = Time::steadyNow();
        for (auto& k : s->m_keyboards)
            if (k)
                k->sendKey(now, code, down ? WL_KEYBOARD_KEY_STATE_PRESSED : WL_KEYBOARD_KEY_STATE_RELEASED);
        lastKeys = Time::steadyNow();
        xkb_state_update_key(state, code + 8, down ? XKB_KEY_DOWN : XKB_KEY_UP);
        mods(s);
        frame.trigger |=
            down &&
            placement::triggers(
                code, xkb_state_mod_names_are_active(state, XKB_STATE_MODS_EFFECTIVE, XKB_STATE_MATCH_ANY, XKB_MOD_NAME_CTRL, XKB_MOD_NAME_ALT, XKB_MOD_NAME_LOGO, nullptr) > 0);
    }

    void SSession::deliverPointer(SFrame& frame, eis_event* e) {
        const auto& w    = frame.window;
        const auto& s    = frame.seat;
        const auto  type = eis_event_get_type(e);
        if (type == EIS_EVENT_POINTER_MOTION_ABSOLUTE) {
            const double x = eis_event_pointer_get_absolute_x(e), y = eis_event_pointer_get_absolute_y(e);
            if (!std::isfinite(x) || !std::isfinite(y))
                return;
            // A point over one of the window's popups reaches the popup, also outside the window. Any
            // other point is clamped to the window, keeping the fractions of its last pixel.
            const auto size = w->size(Desktop::View::IGeometric::GEOMETRIC_GOAL);
            position        = popupAt(w, {x, y}) ? Vector2D{x, y} : Vector2D{std::clamp(x, 0.0, lastInside(size.x)), std::clamp(y, 0.0, lastInside(size.y))};
            pointerAt       = {x, y};
        }
        if (type == EIS_EVENT_BUTTON_BUTTON) {
            const auto button = eis_event_button_get_button(e);
            const bool down   = eis_event_button_get_is_press(e);
            // sendLeave already releases buttons during a same-client switch.
            if (down ? buttons.contains(button) : !buttons.contains(button))
                return;
        }
        // A private drag takes the pointer's motion and the release of its button, which drops, and the
        // window's pointer gets nothing meanwhile.
        if (auto d = activeDrag()) {
            if (type == EIS_EVENT_POINTER_MOTION_ABSOLUTE)
                dragMotion(d, w);
            else if (type == EIS_EVENT_BUTTON_BUTTON && !eis_event_button_get_is_press(e)) {
                const auto button = eis_event_button_get_button(e);
                buttons.erase(button);
                if (button == dragButton) {
                    dragButton = 0;
                    d->drop();
                }
                frame.trigger = true;
            }
            cursor::show(w, position, false);
            return;
        }
        Vector2D local;
        auto     target = surfaceAt(w, position, local);
        if (!target)
            return;
        // A press brings the keyboard focus with it, right before the button, as Hyprland's click to
        // focus does: apps such as LibreOffice act on the focus a click gives, and would otherwise get it
        // only with the first key, after the click. It goes to the window's own surface, where keys go,
        // also for a click into a popup. Not on the connection of the real keyboard focus, whose window
        // would lose it to every click; keys take it there, see keepKeyboard.
        if (type == EIS_EVENT_BUTTON_BUTTON && eis_event_button_get_is_press(e) && !enteredKeyboard && w->resource() && !sharesKeyboardFocus()) {
            enterKeyboard(s, w->resource(), false);
            frame.focusedKeyboard = true;
        }
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
        for (auto& p : s->m_pointers)
            if (p)
                sendPointerEvent(frame, p, e, target, local);
        cursor::show(w, position, type == EIS_EVENT_BUTTON_BUTTON && eis_event_button_get_is_press(e));
        if (type == EIS_EVENT_BUTTON_BUTTON) {
            frame.trigger = true;
            if (eis_event_button_get_is_press(e))
                buttons.insert(eis_event_button_get_button(e));
            else {
                buttons.erase(eis_event_button_get_button(e));
                std::erase_if(pressSerials, [&](const SPress& p) { return p.button == eis_event_button_get_button(e); });
            }
        }
    }

    void SSession::sendPointerEvent(const SFrame& frame, const WP<CWLPointerResource>& p, eis_event* e, const SP<CWLSurfaceResource>& target, const Vector2D& local) {
        const auto& s    = frame.seat;
        const auto  now  = frame.time;
        const auto  type = eis_event_get_type(e);
        switch (type) {
            case EIS_EVENT_POINTER_MOTION_ABSOLUTE: p->sendMotion(now, local); break;
            case EIS_EVENT_BUTTON_BUTTON: {
                auto       button = eis_event_button_get_button(e);
                bool       down   = eis_event_button_get_is_press(e);
                const auto last   = wl_display_get_serial(g_pCompositor->m_wlDisplay);
                p->sendButton(now, button, down ? WL_POINTER_BUTTON_STATE_PRESSED : WL_POINTER_BUTTON_STATE_RELEASED);
                if (down) {
                    // The press's serial, which Hyprland takes as the next one when it sends the press.
                    if (const auto serial = wl_display_get_serial(g_pCompositor->m_wlDisplay); serial != last)
                        rememberPress(serial, button);
                    g_pSeatManager->clearPointerButtonSerials(s, target, button);
                }
                break;
            }
            case EIS_EVENT_SCROLL_DELTA:
            case EIS_EVENT_SCROLL_DISCRETE:
            case EIS_EVENT_SCROLL_STOP: sendScroll(p, e, now); break;
            default: break;
        }
    }

    void SSession::restorePointer(const SP<CWLSeatResource>& s, const SP<CWLSurfaceResource>& real) {
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
        selection::restore(s->client());
    }

    int SSession::tick() {
        auto w = window.lock();
        if (!w)
            return 0;
        maybeResume();
        resumeHeld();
        const auto now = Time::steadyNow();
        // While the sender holds keys, only the user's input or the sender's going away ends it.
        if (keyboardKept && keys.empty() && now >= keysSettled)
            returnKeyboard(false);
        const auto held = leaseState();
        if (!held)
            lease.reset();
        const bool recent = std::chrono::duration_cast<std::chrono::milliseconds>(now - lastInput).count() < IDLE_MS;
        if (!recent && !held && keys.empty() && buttons.empty()) {
            leave();
            releaseX();
        }
        // When a send to a sender fails, as it does once the sender has exited, libeis closes the connection
        // without a disconnect event, and what the sender held stays pressed. A failed ping it does report, so a
        // sender that has sent nothing for a second gets one every second.
        if (client && !recent && now - lastPing >= std::chrono::milliseconds(PING_INTERVAL_MS)) {
            lastPing = now;
            if (auto* ping = eis_client_new_ping(client)) {
                eis_ping(ping);
                eis_ping_unref(ping);
            }
        }
        // Hidden windows do not get compositor frame callbacks, and Hyprland tells them that they are suspended, which
        // stops some apps from drawing at all. For a second after input, and while a sender has its devices, which it
        // keeps while it pauses between inputs and closes when it exits, also when libeis does not report the
        // disconnect, they get frame callbacks and are not suspended.
        if (recent || (client && (pointer || keyboard))) {
            if (!awake)
                awake.emplace(w);
            content->sendFrames();
            wl_event_source_timer_update(timer, TICK_MS);
            return 0;
        }
        awake.reset();
        if (held || client) {
            // A lease keeps the emulated focus until it ends, and a sender gets its pings.
            const int delay = held ? std::max(1, held->remainingMs) : PING_INTERVAL_MS;
            wl_event_source_timer_update(timer, client ? std::min(delay, PING_INTERVAL_MS) : delay);
        }
        return 0;
    }

    int SSession::poll() {
        eis_dispatch(context);
        process();
        return 0;
    }

    void SSession::answerPings() {
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

    void SSession::process() {
        while (!holding()) {
            auto* e = stashed ? std::exchange(stashed, nullptr) : eis_get_event(context);
            if (!e) {
                if (restoreDue)
                    hold(lastKeys + SATELLITE_BATCH);
                break;
            }
            if (leavesSatelliteTooSoon(e)) {
                stashed = e;
                hold(lastKeys + SATELLITE_BATCH);
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
                    if (pending.size() < MAX_PENDING)
                        pending.push_back(eis_event_ref(e));
                    else if (client)
                        eis_client_disconnect(client);
                    break;
                case EIS_EVENT_FRAME:
                    if (d == pointer || d == keyboard)
                        deliver(d);
                    break;
                case EIS_EVENT_DEVICE_START_EMULATING:
                    if (d == keyboard)
                        keyboardEmulating = true;
                    break;
                case EIS_EVENT_DEVICE_STOP_EMULATING:
                    if (d == pointer || d == keyboard) {
                        if (d == keyboard)
                            releaseKeyboard();
                        if (d == pointer)
                            releasePointer();
                        if (d == keyboard)
                            keyboardEmulating = false;
                        // The keyboard focus that a click gave ends with a sender whose keyboard is not emulating.
                        if (d == pointer && !keyboardEmulating)
                            releaseKeyboard();
                        drop(d);
                        scheduleIdle();
                    }
                    break;
                default: break;
            }
            eis_event_unref(e);
        }
    }
}
