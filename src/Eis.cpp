#include "Eis.hpp"
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/desktop/state/FocusState.hpp>
#include <hyprland/src/desktop/state/ViewState.hpp>
#include <hyprland/src/desktop/view/Popup.hpp>
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
#include <stdexcept>

namespace hyprmasq {
    namespace {
        struct Session;
        std::map<std::string, std::unique_ptr<Session>> sessions;
        CFunctionHook*                                  popupHook = nullptr;
        CFunctionHook*                                  dragHook  = nullptr;

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

            Session(const PHLWINDOW& w, std::string p) : window(w), path(std::move(p)) {}
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
                std::erase_if(pending, [&](eis_event* e) {
                    if (eis_event_get_device(e) != d)
                        return false;
                    eis_event_unref(e);
                    return true;
                });
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
                seat   = nullptr;
                client = nullptr;
            }
            SP<CWLSeatResource> wlSeat() {
                auto w = window.lock();
                auto s = w ? w->resource() : nullptr;
                return s ? g_pSeatManager->seatResourceForClient(s->client()) : nullptr;
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
                eis_device_resume(keyboard);
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
                    std::erase_if(pending, [&](eis_event* e) {
                        if (eis_event_get_device(e) != device)
                            return false;
                        eis_event_unref(e);
                        return true;
                    });
                    return;
                }
                if (std::ranges::none_of(pending, [device](eis_event* e) { return eis_event_get_device(e) == device; }))
                    return;
                // Another socket may address a second window on this same Wayland client.
                for (auto& [_, other] : sessions) {
                    if (other.get() != this && other->wlSeat() == s)
                        other->leave();
                }
                const bool isKeyboard = device == keyboard;
                auto       real       = (isKeyboard ? g_pSeatManager->m_state.keyboardFocus : g_pSeatManager->m_state.pointerFocus).lock();
                const bool restore    = real && real->client() == s->client();
                // Resource focus is client-local. Restore it before the event loop can dispatch real input.
                const auto now = Time::millis(Time::steadyNow());
                for (auto* e : pending) {
                    if (eis_event_get_device(e) != device)
                        continue;
                    const auto type = eis_event_get_type(e);
                    if (isKeyboard) {
                        auto target = w->resource();
                        if (!enteredKeyboard) {
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
                        uint32_t code = eis_event_keyboard_get_key(e);
                        bool     down = eis_event_keyboard_get_key_is_press(e);
                        if (down ? !keys.insert(code).second : !keys.erase(code))
                            continue;
                        for (auto& k : s->m_keyboards)
                            if (k)
                                k->sendKey(now, code, down ? WL_KEYBOARD_KEY_STATE_PRESSED : WL_KEYBOARD_KEY_STATE_RELEASED);
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
                        Vector2D local;
                        auto     target = Desktop::viewState()->hitTest().windowSurfaceAt(w->position(Desktop::View::IGeometric::GEOMETRIC_GOAL) + position, w, local);
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
                std::erase_if(pending, [&](eis_event* e) {
                    if (eis_event_get_device(e) != device)
                        return false;
                    eis_event_unref(e);
                    return true;
                });
                if (!isKeyboard)
                    for (auto& p : s->m_pointers)
                        if (p)
                            p->sendFrame();
                if (restore) {
                    if (isKeyboard) {
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
                    } else {
                        auto       surf  = Desktop::View::CWLSurface::fromResource(real);
                        auto       box   = surf ? surf->getSurfaceBoxGlobal() : std::nullopt;
                        const auto local = g_pInputManager->getMouseCoordsInternal() - (box ? box->pos() : Vector2D{});
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
                lastInput = Time::steadyNow();
                wl_event_source_timer_update(timer, 16);
            }
            int tick() {
                auto w = window.lock();
                if (!w)
                    return 0;
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
                while (auto* e = eis_get_event(context)) {
                    auto d = eis_event_get_device(e);
                    switch (eis_event_get_type(e)) {
                        case EIS_EVENT_CLIENT_CONNECT: {
                            auto c = eis_event_get_client(e);
                            if (client || !eis_client_is_sender(c)) {
                                eis_client_disconnect(c);
                                break;
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
                                std::erase_if(pending, [&](eis_event* queued) {
                                    if (eis_event_get_device(queued) != d)
                                        return false;
                                    eis_event_unref(queued);
                                    return true;
                                });
                            }
                            break;
                        default: break;
                    }
                    eis_event_unref(e);
                }
                return 0;
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
            reinterpret_cast<Fn>(popupHook->m_original)(self, popup);
        }
        void startDrag(CWLDataDeviceProtocol* self, WP<CWLDataSourceResource> source, SP<CWLSurfaceResource> icon, SP<CWLSurfaceResource> origin) {
            if (protectedSurface(origin)) {
                if (source)
                    source->cancelled();
                return;
            }
            using Fn = void (*)(CWLDataDeviceProtocol*, WP<CWLDataSourceResource>, SP<CWLSurfaceResource>, SP<CWLSurfaceResource>);
            reinterpret_cast<Fn>(dragHook->m_original)(self, source, icon, origin);
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
        auto install = [&](const char* name, void* replacement) {
            const auto matches = HyprlandAPI::findFunctionsByName(handle, name);
            if (matches.size() != 1)
                throw std::runtime_error(std::string("hyprmasq: ambiguous or missing hook ") + name);
            auto* hook = HyprlandAPI::createFunctionHook(handle, matches[0].address, replacement);
            if (!hook || !hook->hook())
                throw std::runtime_error(std::string("hyprmasq: cannot hook ") + name);
            return hook;
        };
        popupHook = install("addOrStartGrab", reinterpret_cast<void*>(&popupGrab));
        try {
            dragHook = install("initiateDrag", reinterpret_cast<void*>(&startDrag));
        } catch (...) {
            popupHook->unhook();
            popupHook = nullptr;
            throw;
        }
    }
    void shutdownEis() {
        sessions.clear();
        if (popupHook)
            popupHook->unhook();
        if (dragHook)
            dragHook->unhook();
        popupHook = dragHook = nullptr;
    }
    std::expected<std::string, std::string> openEis(const PHLWINDOW& w) {
        if (w->m_isX11)
            return std::unexpected("X11 windows are not supported");
        const auto address = windowAddress(w);
        if (sessions.contains(address))
            return sessions.at(address)->path;
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
        auto  session = std::make_unique<Session>(w, std::string(runtime) + "/hyprmasq/" + g_pCompositor->m_instanceSignature + "/" + address + ".eis");
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
        s->keyboardFocus = g_pSeatManager->m_events.keyboardFocusChange.listen([s] { s->enteredKeyboard.reset(); });
        s->pointerFocus  = g_pSeatManager->m_events.pointerFocusChange.listen([s] { s->enteredPointer.reset(); });
        auto path        = s->path;
        sessions.emplace(address, std::move(session));
        return path;
    }
    bool closeEis(const std::string& address) {
        return sessions.erase(address) != 0;
    }
    std::vector<std::pair<std::string, std::string>> listEis() {
        std::vector<std::pair<std::string, std::string>> result;
        for (const auto& [address, session] : sessions)
            result.emplace_back(address, session->path);
        return result;
    }
}
