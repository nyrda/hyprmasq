#include "FocusPolicy.hpp"
#include "Hooks.hpp"
#include "Launch.hpp"
#include "PrivateAccess.hpp"
#include "Selection.hpp"
#include "Session.hpp"
#include "X11.hpp"
#include <hyprland/src/desktop/state/FocusState.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/devices/IKeyboard.hpp>
#include <hyprland/src/event/EventBus.hpp>
#include <hyprland/src/managers/SeatManager.hpp>
#include <hyprland/src/protocols/XDGShell.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#include <hyprland/src/protocols/core/DataDevice.hpp>
#include <hyprland/src/protocols/core/Seat.hpp>
#include <algorithm>
#include <ranges>
#include <utility>
#include <vector>

namespace hyprmasq::input {
    namespace {
        void*               originalPopupGrab     = nullptr;
        void*               originalStartDrag     = nullptr;
        void*               originalActivateX11   = nullptr;
        void*               originalWindowFocus   = nullptr;
        void*               originalActivate      = nullptr;
        void*               originalUpdateDecos   = nullptr;
        void*               originalKeyboardFocus = nullptr;
        void*               originalPointerFocus  = nullptr;
        void*               originalKeyboardKey   = nullptr;
        void*               originalKeyboardMods  = nullptr;
        void*               originalPointerButton = nullptr;
        void*               originalTouchDown     = nullptr;
        CHyprSignalListener realKey;

        // The session of the driven window that the surface belongs to, unless that window is the active one.
        SSession* protectedSession(const SP<CWLSurfaceResource>& surface) {
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
        void privateDrag(SSession* session, const SP<CWLDataSourceResource>& source, const SP<CWLSurfaceResource>& icon) {
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
            // Without a driven X11 window, finding the window's X server, which reads /proc, is not needed.
            if (std::ranges::none_of(sessions, [](const auto& entry) { return entry.second->xServer() != nullptr; }))
                return false;
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
        // As for X11 apps, a Wayland app with a driven window cannot take the focus by asking for activation, which apps do
        // in answer to input, as a terminal does for its bell or a page through window.focus(): the driven window itself
        // never, and its app's other windows while the user is in another app. The app is the window's connection.
        bool drivenWaylandApp(const PHLWINDOW& w) {
            if (!w || w->m_isX11 || !w->resource())
                return false;
            const auto* client  = w->resource()->client();
            const auto  focused = Desktop::focusState()->window();
            const bool  inApp   = focused && focused->resource() && focused->resource()->client() == client;
            return std::ranges::any_of(sessions, [&](const auto& entry) {
                const auto driven = entry.second->window.lock();
                return driven && !driven->m_isX11 && driven->resource() && driven->resource()->client() == client && (driven == w || !inApp);
            });
        }
        // xwayland-satellite forwards activation requests as xdg-activation. A forced activation comes from the user,
        // for example through a taskbar. A window that the plugin placed silently, or of a Wayland app with a driven
        // window, is marked urgent but not focused, also with focus_on_activate, so that a popup put next to its driven
        // window cannot follow up by taking the focus, and the agent's input cannot move the user's focus or workspace.
        void activate(Desktop::View::CWindow* self, bool force) {
            if (!force && drivenX11App(self->m_self.lock(), false))
                return;
            using Fn              = void (*)(Desktop::View::CWindow*, bool);
            const auto suppressed = self->m_suppressedEvents;
            if (!force && (launch::placed(self->m_self.lock()) || drivenWaylandApp(self->m_self.lock())))
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
            CKeptFocus(const SP<CWLSurfaceResource>& next, WP<CWLSurfaceResource> SSession::* entered, std::vector<WP<Resource>> CWLSeatResource::* resources,
                       bool SSession::* keeping) : m_keeping(keeping) {
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
            std::vector<SSession*>                                       m_kept;
            bool SSession::* m_keeping;
        };
        // The user's real input to the client of a private drag cancels the drag before the input arrives.
        void setKeyboardFocus(CSeatManager* self, SP<CWLSurfaceResource> surface) {
            if (surface) {
                drag::cancelClient(surface->client(), "user_input");
                selection::userInput(surface->client());
            }
            CKeptFocus<CWLKeyboardResource> kept(surface, &SSession::enteredKeyboard, &CWLSeatResource::m_keyboards, &SSession::keepingKeyboard);
            using Fn = void (*)(CSeatManager*, SP<CWLSurfaceResource>);
            reinterpret_cast<Fn>(originalKeyboardFocus)(self, surface);
        }
        void setPointerFocus(CSeatManager* self, SP<CWLSurfaceResource> surface, const Vector2D& local) {
            if (surface)
                drag::cancelClient(surface->client(), "user_input");
            CKeptFocus<CWLPointerResource> kept(surface, &SSession::enteredPointer, &CWLSeatResource::m_pointers, &SSession::keepingPointer);
            using Fn = void (*)(CSeatManager*, SP<CWLSurfaceResource>, const Vector2D&);
            reinterpret_cast<Fn>(originalPointerFocus)(self, surface, local);
        }

        // The user's own keys, modifiers and buttons take the keyboard focus of their connection back from a window that
        // keeps it while its keys settle, before Hyprland sends them, so that they reach the user's window. Keys also
        // through the event bus, which sees them before Hyprland passes them to an input method's keyboard grab instead of
        // the seat: the input method then commits its text to the user's window.
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
    }

    void initFocusPolicy(void* handle) {
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
        realKey = Event::bus()->m_events.input.keyboard.key.listen(
            [](const IKeyboard::SKeyEvent&, Event::SCallbackInfo&) { returnKeyboards(g_pSeatManager->m_state.keyboardFocusResource); });
    }

    void shutdownFocusPolicy() {
        realKey.reset();
    }
}
