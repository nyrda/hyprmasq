#include "Eis.hpp"
#include "Cursor.hpp"
#include "Drag.hpp"
#include "Hooks.hpp"
#include "Launch.hpp"
#include "Placement.hpp"
#include "Selection.hpp"
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
        void*                                           originalKeyboardKey   = nullptr;
        void*                                           originalKeyboardMods  = nullptr;
        void*                                           originalPointerButton = nullptr;
        void*                                           originalTouchDown     = nullptr;
        CHyprSignalListener                             monitorLayout;

        // How long a window of the connection with the real keyboard focus keeps the emulated focus after a command key,
        // see Session::keepKeyboard. Chromium answers a shortcut 10 to 90 ms after the key on a hidden workspace.
        constexpr auto KEYS_SETTLE = std::chrono::milliseconds(250);

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
        // The buttons that a pointer resource releases when it leaves its surface, outside Hyprland's own drags.
        std::vector<uint32_t>& pressedOf(CWLPointerResource& pointer);
        template <std::vector<uint32_t> CWLPointerResource::* Pressed>
        struct SPressedAccess {
            friend std::vector<uint32_t>& pressedOf(CWLPointerResource& pointer) {
                return pointer.*Pressed;
            }
        };
        template struct SPressedAccess<&CWLPointerResource::m_pressedButtons>;

        // True if focus is on a surface of the client, or of the window's Wayland connection.
        bool sharesConnection(wl_client* client, const WP<CWLSurfaceResource>& focus) {
            auto surface = focus.lock();
            return surface && client && surface->client() == client;
        }
        bool sharesConnection(const PHLWINDOW& w, const WP<CWLSurfaceResource>& focus) {
            return w->resource() && sharesConnection(w->resource()->client(), focus);
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
            bool                                 keyboardEmulating = false; // between the keyboard's start and stop of emulation
            Time::steady_tp                      pausedAt, holdUntil = Time::steadyNow(), lastKeys = Time::steadyNow(), lastPing;
            eis_event*                           stashed     = nullptr; // taken from libeis, waiting for a hold to end
            wl_client*                           server      = nullptr; // the window's X server, looked up once
            bool                                 serverKnown = false;
            std::vector<SPointerRegion>          regions; // of the pointer device, as announced
            wl_event_source*                     regionCheck = nullptr;
            std::optional<SLease>                lease;
            // Set while Hyprland changes the real focus and a lease keeps this window's emulated focus, see CKeptFocus.
            bool keepingKeyboard = false, keepingPointer = false;
            // While the window keeps the emulated keyboard focus on the connection of the real focus, see keepKeyboard:
            // that connection's seat, and when it gives it back without further keys.
            bool                         keyboardKept = false;
            WP<CWLSeatResource>          keptSeat;
            Time::steady_tp              keysSettled;
            Time::steady_tp              lastCommand;     // the last press of a commandKey
            uint64_t                     interrupted = 0; // times the user's own input took the keyboard back before that one settled
            std::shared_ptr<SDragRecord> drags       = std::make_shared<SDragRecord>();
            // The serials of the presses that the window got from the sender, while their buttons are held: only they
            // start a private drag.
            struct SPress {
                uint32_t serial = 0, button = 0;
            };
            std::vector<SPress>               pressSerials;
            std::weak_ptr<drag::CPrivateDrag> drag;
            uint32_t                          dragButton = 0;               // the held button that started the drag, its drop
            Vector2D                          pointerAt  = position;        // the last motion, also outside the window
            std::string_view                  closing    = "socket_closed"; // why the session ends, for its drag

            Session(const PHLWINDOW& w, std::string p, bool f) : window(w), path(std::move(p)), content(std::make_shared<wait::CContentWatch>(w)), force(f) {}
            ~Session() {
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
            void clearDevice(eis_device*& d) {
                if (!d)
                    return;
                drop(d);
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
            void clearClient() {
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
            void leavePointer() {
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
            void leave() {
                leaveKeyboard();
                leavePointer();
            }
            // Counts a drag-and-drop of the window's app that the plugin cancelled before it could start, and why.
            void dragCancelled(std::string_view reason) {
                ++drags->id;
                ++drags->cancelled;
                drags->phase     = "cancelled";
                drags->privately = false;
                drags->reason    = reason;
                drags->endedAt   = Time::steadyNow();
                drags->end(drags->id, "cancelled", reason);
                drags->changed.emit();
            }
            // The private drag of the window's app, before its drop.
            std::shared_ptr<drag::CPrivateDrag> activeDrag() const {
                auto d = drag.lock();
                return d && d->active() ? d : nullptr;
            }
            void cancelDrag(std::string_view reason) {
                if (auto d = activeDrag())
                    d->cancel(reason);
            }
            // Why a drag that the window's app starts cannot run as a private drag, or none when it can: see `drag` in
            // docs/mcp.md.
            std::optional<std::string_view> dragRefusal(const drag::SRequest* request, const SP<CWLDataSourceResource>& source, const SP<CWLSurfaceResource>& icon) {
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
            void beginDrag(const drag::SRequest& request, const SP<CWLDataSourceResource>& source, const SP<CWLSurfaceResource>& icon) {
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
            // Moves the drag to the surface under the emulated pointer. Only the window's own surfaces take part: a point
            // outside the window and its popups is over none, and the drag leaves the surface it was over.
            void dragMotion(const std::shared_ptr<drag::CPrivateDrag>& d, const PHLWINDOW& w) {
                const auto             size = w->size(Desktop::View::IGeometric::GEOMETRIC_GOAL);
                SP<CWLSurfaceResource> target;
                Vector2D               local;
                if (popupAt(w, pointerAt) || (pointerAt.x >= 0 && pointerAt.y >= 0 && pointerAt.x < size.x && pointerAt.y < size.y))
                    target = surfaceAt(w, pointerAt, local);
                d->motion(target, local);
            }
            // Lets go of the keys the sender holds. Without a lease the keyboard leaves the window, which releases them
            // for the application. With one, or while the window keeps the focus for a shortcut to settle, the focus
            // stays, so each key goes up.
            void releaseKeyboard() {
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
            // Lets go of the buttons the sender holds. Without a lease the pointer leaves the window; with one it stays
            // where it is.
            void releasePointer() {
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
                            self->cancelDrag("window_moved");
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
                if (content)
                    content->lastInput = lastInput;
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
            // Apps such as Chromium take the window with the keyboard focus for the active one, and run a shortcut only
            // once the page has let its key go by, after the focus would have gone back to the user's window. A window of
            // the connection with the real focus keeps the emulated focus while its sender holds keys and until a command
            // key has settled; after typing, it goes back with every frame. The user's own input to the connection takes
            // it back first, see returnKeyboard, so that it reaches the user's window.
            void keepKeyboard(const SP<CWLSeatResource>& s) {
                keyboardKept = true;
                keptSeat     = s;
                keysSettled  = Time::steadyNow() + KEYS_SETTLE;
            }
            // Gives the connection's keyboard focus back to the surface of the real focus, also when the window's surface
            // is gone. byUser: the user's own input to the connection is about to be sent, which interrupts a command key
            // that has not settled.
            void returnKeyboard(bool byUser) {
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
                selection::restore(s->client());
            }
            void mods(const SP<CWLSeatResource>& s, bool toSender = true) {
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
            void rememberPress(uint32_t serial, uint32_t button) {
                if (pressSerials.size() >= 8)
                    pressSerials.erase(pressSerials.begin());
                pressSerials.push_back({.serial = serial, .button = button});
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
                        other->cancelDrag("other_window");
                        other->lease.reset();
                        other->leave();
                    }
                }
                const bool isKeyboard = device == keyboard;
                const auto has     = [&](auto predicate) { return std::ranges::any_of(pending, [&](eis_event* e) { return eis_event_get_device(e) == device && predicate(e); }); };
                const bool presses = !isKeyboard && has([](eis_event* e) { return eis_event_get_type(e) == EIS_EVENT_BUTTON_BUTTON && eis_event_button_get_is_press(e); });
                const bool scrolls =
                    !isKeyboard && has([](eis_event* e) { return eis_event_get_type(e) == EIS_EVENT_SCROLL_DELTA || eis_event_get_type(e) == EIS_EVENT_SCROLL_DISCRETE; });
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
                const auto now             = Time::millis(Time::steadyNow());
                bool       trigger         = false; // the frame has input that can open a window, see placement::triggers
                bool       focusedKeyboard = false; // a press gave the window the keyboard focus
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
                        if (down && commandKey(state, code))
                            lastCommand = Time::steadyNow();
                        for (auto& k : s->m_keyboards)
                            if (k)
                                k->sendKey(now, code, down ? WL_KEYBOARD_KEY_STATE_PRESSED : WL_KEYBOARD_KEY_STATE_RELEASED);
                        lastKeys = Time::steadyNow();
                        xkb_state_update_key(state, code + 8, down ? XKB_KEY_DOWN : XKB_KEY_UP);
                        mods(s);
                        trigger |= down &&
                            placement::triggers(code,
                                                xkb_state_mod_names_are_active(state, XKB_STATE_MODS_EFFECTIVE, XKB_STATE_MATCH_ANY, XKB_MOD_NAME_CTRL, XKB_MOD_NAME_ALT,
                                                                               XKB_MOD_NAME_LOGO, nullptr) > 0);
                    } else {
                        if (type == EIS_EVENT_POINTER_MOTION_ABSOLUTE) {
                            const double x = eis_event_pointer_get_absolute_x(e), y = eis_event_pointer_get_absolute_y(e);
                            if (!std::isfinite(x) || !std::isfinite(y))
                                continue;
                            // A point over one of the window's popups reaches the popup, also outside the window. Any
                            // other point is clamped to the window.
                            const auto size = w->size(Desktop::View::IGeometric::GEOMETRIC_GOAL);
                            position = popupAt(w, {x, y}) ? Vector2D{x, y} : Vector2D{std::clamp(x, 0.0, std::max(0.0, size.x - 1)), std::clamp(y, 0.0, std::max(0.0, size.y - 1))};
                            pointerAt = {x, y};
                        }
                        if (type == EIS_EVENT_BUTTON_BUTTON) {
                            const auto button = eis_event_button_get_button(e);
                            const bool down   = eis_event_button_get_is_press(e);
                            // sendLeave already releases buttons during a same-client switch.
                            if (down ? buttons.contains(button) : !buttons.contains(button))
                                continue;
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
                                trigger = true;
                            }
                            cursor::show(w, position, false);
                            continue;
                        }
                        Vector2D local;
                        auto     target = surfaceAt(w, position, local);
                        if (!target)
                            continue;
                        // A press brings the keyboard focus with it, right before the button, as Hyprland's click to
                        // focus does: apps such as LibreOffice act on the focus a click gives, and would otherwise get it
                        // only with the first key, after the click. It goes to the window's own surface, where keys go,
                        // also for a click into a popup. Not on the connection of the real keyboard focus, whose window
                        // would lose it to every click; keys take it there, see keepKeyboard.
                        if (type == EIS_EVENT_BUTTON_BUTTON && eis_event_button_get_is_press(e) && !enteredKeyboard && w->resource() && !sharesKeyboardFocus()) {
                            enterKeyboard(s, w->resource(), false);
                            focusedKeyboard = true;
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
                        for (auto& p : s->m_pointers) {
                            if (!p)
                                continue;
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
                            trigger = true;
                            if (eis_event_button_get_is_press(e))
                                buttons.insert(eis_event_button_get_button(e));
                            else {
                                buttons.erase(eis_event_button_get_button(e));
                                std::erase_if(pressSerials, [&](const SPress& p) { return p.button == eis_event_button_get_button(e); });
                            }
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
                    else if (isKeyboard && !w->m_isX11 && real != w->resource() && (!keys.empty() || Time::steadyNow() < lastCommand + KEYS_SETTLE))
                        keepKeyboard(s);
                    else if (isKeyboard) {
                        keyboardKept = false;
                        keptSeat.reset();
                        restoreKeyboard(s, real);
                    } else {
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
                }
                // The user's window on the connection gets the user's clipboard back after a press, which kept the
                // keyboard focus there.
                if (!isKeyboard && presses && sharesKeyboardFocus())
                    selection::restore(s->client());
                if (stack)
                    x11::lower(*stack);
                // xwayland-satellite moves X focus once it has handled the batch that entered the window, so keys after
                // the click wait for a later batch, as after an enter for keys.
                if (focusedKeyboard && !w->m_isX11 && xServer())
                    hold();
                // X focus goes back at once when the user's keyboard is on the same Wayland client, otherwise once input
                // pauses.
                auto realKeyboard = g_pSeatManager->m_state.keyboardFocus.lock();
                if (takesFocus && realKeyboard && realKeyboard->client() == s->client())
                    x11::release(w);
                lastInput          = Time::steadyNow();
                commitsAtInput     = content->commits;
                content->lastInput = lastInput;
                placement::emulatedInput(w, trigger);
                // Input that comes more often than every 16 ms, as a drag's motion does, keeps putting tick() off, so the
                // frame callbacks that a hidden window needs to go on, such as Firefox before it starts a drag, go out
                // here too, at most every 16 ms.
                content->sendFrames();
                wl_event_source_timer_update(timer, 16);
            }
            int tick() {
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
        };

        void closeClosedWindow(const std::string& address) {
            if (const auto it = sessions.find(address); it != sessions.end()) {
                it->second->closing = "window_closed";
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

        // The session of the driven window that the surface belongs to, unless that window is the active one.
        Session* protectedSession(const SP<CWLSurfaceResource>& surface) {
            for (auto& [_, session] : sessions) {
                auto w = session->window.lock();
                if (!w || w == Desktop::focusState()->window())
                    continue;
                bool found = false;
                forEachSurface(w, [&](SP<CWLSurfaceResource> s) { found |= s == surface; });
                if (found)
                    return session.get();
            }
            return nullptr;
        }
        bool protectedSurface(const SP<CWLSurfaceResource>& surface) {
            return protectedSession(surface) != nullptr;
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
        // Hyprland runs a drag-and-drop as a grab of the seat, which takes the user's pointer, so a drag from a driven
        // window that is not active runs as a private drag of its app, see Drag.hpp, when emulated input started it, or
        // is cancelled. Either is counted for its socket.
        void privateDrag(Session* session, const SP<CWLDataSourceResource>& source, const SP<CWLSurfaceResource>& icon) {
            const auto* request = drag::request();
            if (const auto refused = session->dragRefusal(request, source, icon)) {
                if (source)
                    source->cancelled();
                session->dragCancelled(*refused);
                return;
            }
            session->beginDrag(*request, source, icon);
        }
        void startDrag(CWLDataDeviceProtocol* self, WP<CWLDataSourceResource> source, SP<CWLSurfaceResource> icon, SP<CWLSurfaceResource> origin) {
            if (auto* session = protectedSession(origin)) {
                privateDrag(session, source.lock(), icon);
                return;
            }
            // Hyprland's drag sends its offers to the data device of the client under the user's pointer, so a private
            // drag of the client ends first.
            if (origin)
                drag::cancelClient(origin->client(), "user_input");
            using Fn = void (*)(CWLDataDeviceProtocol*, WP<CWLDataSourceResource>, SP<CWLSurfaceResource>, SP<CWLSurfaceResource>);
            reinterpret_cast<Fn>(originalStartDrag)(self, source, icon, origin);
        }

        // A drag without a source stays within its client, as a private drag does. Hyprland's handler cannot take one, so
        // one from a window that is not driven, or is active, is ignored: there is no source to tell.
        void sourcelessDrag(const SP<CWLSurfaceResource>& origin, const SP<CWLSurfaceResource>& icon) {
            if (auto* session = protectedSession(origin))
                privateDrag(session, nullptr, icon);
            else
                Log::logger->log(Log::WARN, "[hyprmasq] ignoring a drag without a source");
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
        // for example through a taskbar. A window that the plugin placed silently is marked urgent but not focused, also
        // with focus_on_activate, so that a popup put next to its driven window cannot follow up by taking the focus.
        void activate(Desktop::View::CWindow* self, bool force) {
            if (!force && drivenX11App(self->m_self.lock(), false))
                return;
            using Fn              = void (*)(Desktop::View::CWindow*, bool);
            const auto suppressed = self->m_suppressedEvents;
            if (!force && launch::placed(self->m_self.lock()))
                self->m_suppressedEvents |= Desktop::View::SUPPRESS_ACTIVATE_FOCUSONLY;
            reinterpret_cast<Fn>(originalActivate)(self, force);
            self->m_suppressedEvents = suppressed;
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
        // The user's real input to the client of a private drag cancels the drag before the input arrives.
        void setKeyboardFocus(CSeatManager* self, SP<CWLSurfaceResource> surface) {
            if (surface) {
                drag::cancelClient(surface->client(), "user_input");
                selection::userInput(surface->client());
            }
            CKeptFocus<CWLKeyboardResource> kept(surface, &Session::enteredKeyboard, &CWLSeatResource::m_keyboards, &Session::keepingKeyboard);
            using Fn = void (*)(CSeatManager*, SP<CWLSurfaceResource>);
            reinterpret_cast<Fn>(originalKeyboardFocus)(self, surface);
        }
        void setPointerFocus(CSeatManager* self, SP<CWLSurfaceResource> surface, const Vector2D& local) {
            if (surface)
                drag::cancelClient(surface->client(), "user_input");
            CKeptFocus<CWLPointerResource> kept(surface, &Session::enteredPointer, &CWLSeatResource::m_pointers, &Session::keepingPointer);
            using Fn = void (*)(CSeatManager*, SP<CWLSurfaceResource>, const Vector2D&);
            reinterpret_cast<Fn>(originalPointerFocus)(self, surface, local);
        }

        // The user's own keys, modifiers and buttons take the keyboard focus of their connection back from a window that
        // keeps it while its keys settle, before Hyprland sends them, so that they reach the user's window.
        void returnKeyboards(const WP<CWLSeatResource>& to) {
            const auto seat = to.lock();
            if (!seat)
                return;
            selection::userInput(seat->client());
            for (auto& [_, session] : sessions) {
                const auto kept = session->keptSeat.lock();
                if (session->keyboardKept && kept && kept->client() == seat->client())
                    session->returnKeyboard(true);
            }
        }
        void sendKeyboardKey(CSeatManager* self, uint32_t timeMs, uint32_t key, wl_keyboard_key_state state) {
            returnKeyboards(self->m_state.keyboardFocusResource);
            using Fn = void (*)(CSeatManager*, uint32_t, uint32_t, wl_keyboard_key_state);
            reinterpret_cast<Fn>(originalKeyboardKey)(self, timeMs, key, state);
        }
        void sendKeyboardMods(CSeatManager* self, uint32_t depressed, uint32_t latched, uint32_t locked, uint32_t group) {
            returnKeyboards(self->m_state.keyboardFocusResource);
            using Fn = void (*)(CSeatManager*, uint32_t, uint32_t, uint32_t, uint32_t);
            reinterpret_cast<Fn>(originalKeyboardMods)(self, depressed, latched, locked, group);
        }
        void sendTouchDown(CSeatManager* self, SP<CWLSurfaceResource> surface, uint32_t timeMs, int32_t id, const Vector2D& local) {
            if (surface) {
                drag::cancelClient(surface->client(), "user_input");
                selection::userInput(surface->client());
            }
            using Fn = void (*)(CSeatManager*, SP<CWLSurfaceResource>, uint32_t, int32_t, const Vector2D&);
            reinterpret_cast<Fn>(originalTouchDown)(self, surface, timeMs, id, local);
        }
        void sendPointerButton(CSeatManager* self, uint32_t timeMs, uint32_t button, wl_pointer_button_state state) {
            returnKeyboards(self->m_state.pointerFocusResource);
            using Fn = void (*)(CSeatManager*, uint32_t, uint32_t, wl_pointer_button_state);
            reinterpret_cast<Fn>(originalPointerButton)(self, timeMs, button, state);
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
        originalKeyboardKey   = hook(handle, "CSeatManager::sendKeyboardKey", reinterpret_cast<void*>(&sendKeyboardKey));
        originalKeyboardMods  = hook(handle, "CSeatManager::sendKeyboardMods", reinterpret_cast<void*>(&sendKeyboardMods));
        originalPointerButton = hook(handle, "CSeatManager::sendPointerButton", reinterpret_cast<void*>(&sendPointerButton));
        originalTouchDown     = hook(handle, "CSeatManager::sendTouchDown", reinterpret_cast<void*>(&sendTouchDown));
        drag::onSourceless(&sourcelessDrag);
        monitorLayout = Event::bus()->m_events.monitor.layoutChanged.listen([] {
            for (auto& [_, session] : sessions)
                session->checkRegionsLater();
        });
    }
    void shutdownEis() {
        monitorLayout.reset();
        for (auto& [_, session] : sessions)
            session->closing = "unloaded";
        sessions.clear();
        drag::shutdown();
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
            // Hyprland sent the connection's keyboards a leave, and the new focus an enter.
            if (!s->keepingKeyboard) {
                s->enteredKeyboard.reset();
                s->keyboardKept = false;
                s->keptSeat.reset();
            }
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
                            .lease            = session->leaseState(),
                            .interrupted      = session->interrupted,
                            .drags            = session->drags};
    }
    bool starved(const PHLWINDOW& window) {
        if (g_pHyprRenderer->shouldRenderWindow(window))
            return false;
        if (auto* session = sessionOf(window); session && session->content->fed())
            return false;
        bool waiting = false;
        forEachSurface(window, [&](SP<CWLSurfaceResource> s) { waiting = waiting || !s->m_current.callbacks.empty(); });
        return waiting;
    }
    SCapabilities capabilities(const PHLWINDOW& window) {
        auto* session = sessionOf(window);
        // Input to a window on the connection of the real focus hands the pointer focus back after every frame, which
        // releases the buttons it held, and the keyboard focus once its keys are up and settled or the user's input comes,
        // which releases its keys. For buttons, that is the connection of the real pointer focus.
        const bool keyboardShared = sharesConnection(window, g_pSeatManager->m_state.keyboardFocus);
        const bool pointerShared  = sharesConnection(window, g_pSeatManager->m_state.pointerFocus);
        const bool isX11          = x11Window(window, session);
        const bool active         = window == Desktop::focusState()->window();
        return {.lease      = !isX11 && !keyboardShared,
                .hold       = !keyboardShared && !pointerShared,
                .dnd        = active,
                .privateDnd = !isX11 && !keyboardShared && !pointerShared && !sharesConnection(window, g_pSeatManager->m_state.touchFocus) && !active && window->resource() &&
                    drag::hasDevice(window->resource()->client()),
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
    bool drivesClient(wl_client* client) {
        return std::ranges::any_of(sessions, [&](const auto& entry) {
            const auto w = entry.second->window.lock();
            return w && w->resource() && w->resource()->client() == client;
        });
    }
    bool emulatedKeyboardOn(wl_client* client) {
        return std::ranges::any_of(sessions, [&](const auto& entry) { return sharesConnection(client, entry.second->enteredKeyboard); });
    }
    bool emulatedPointerOn(wl_client* client) {
        return std::ranges::any_of(sessions, [&](const auto& entry) { return sharesConnection(client, entry.second->enteredPointer); });
    }
    std::vector<SEisInfo> listEis() {
        std::vector<SEisInfo> result;
        for (const auto& [address, session] : sessions)
            result.push_back({.address = address, .path = session->path, .id = session->id, .force = session->force});
        return result;
    }
}
