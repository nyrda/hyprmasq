#include "FocusPolicy.hpp"
#include "Hooks.hpp"
#include "Launch.hpp"
#include "PrivateAccess.hpp"
#include "Selection.hpp"
#include "Session.hpp"
#include "X11.hpp"
#include <hyprland/src/desktop/state/FocusState.hpp>
#include <hyprland/src/desktop/view/window/Window.hpp>
#include <hyprland/src/desktop/view/window/WindowPresentation.hpp>
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
        // The hooked functions, see SPinned at the end of this file.
        using PopupGrab                                 = void (CXDGShellProtocol::*)(SP<CXDGPopupResource>);
        using StartDrag                                 = void (CWLDataDeviceProtocol::*)(WP<CWLDataSourceResource>, SP<CWLSurfaceResource>, SP<CWLSurfaceResource>);
        using ActivationRequest                         = void (Desktop::View::CWindow::*)();
        using WindowFocus                               = void (Desktop::CFocusState::*)(PHLWINDOW, Desktop::eFocusReason, SP<CWLSurfaceResource>);
        using Activate                                  = void (Desktop::View::CWindow::*)(bool);
        using UpdateDecorations                         = void (Desktop::View::CWindowPresentation::*)();
        using KeyboardFocus                             = void (CSeatManager::*)(SP<CWLSurfaceResource>);
        using PointerFocus                              = void (CSeatManager::*)(SP<CWLSurfaceResource>, const Vector2D&);
        using KeyboardKey                               = void (CSeatManager::*)(uint32_t, uint32_t, wl_keyboard_key_state);
        using KeyboardMods                              = void (CSeatManager::*)(uint32_t, uint32_t, uint32_t, uint32_t);
        using PointerButton                             = void (CSeatManager::*)(uint32_t, uint32_t, wl_pointer_button_state);
        using TouchDown                                 = void (CSeatManager::*)(SP<CWLSurfaceResource>, uint32_t, int32_t, const Vector2D&);
        HookFn<PopupGrab>         originalPopupGrab     = nullptr;
        HookFn<StartDrag>         originalStartDrag     = nullptr;
        HookFn<ActivationRequest> originalActivateX11   = nullptr;
        HookFn<WindowFocus>       originalWindowFocus   = nullptr;
        HookFn<Activate>          originalActivate      = nullptr;
        HookFn<UpdateDecorations> originalUpdateDecos   = nullptr;
        HookFn<KeyboardFocus>     originalKeyboardFocus = nullptr;
        HookFn<PointerFocus>      originalPointerFocus  = nullptr;
        HookFn<KeyboardKey>       originalKeyboardKey   = nullptr;
        HookFn<KeyboardMods>      originalKeyboardMods  = nullptr;
        HookFn<PointerButton>     originalPointerButton = nullptr;
        HookFn<TouchDown>         originalTouchDown     = nullptr;
        CHyprSignalListener       realKey;
        // The popups that asked for a grab, while they live.
        std::vector<WP<CXDGPopupResource>> grabs;

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
            std::erase_if(grabs, [](const WP<CXDGPopupResource>& p) { return p.expired(); });
            if (popup)
                grabs.emplace_back(popup);
            // The new popup need not be mapped or in the window's popup tree yet.
            auto parent = popup->m_parent.lock();
            while (parent) {
                if (protectedSurface(parent->m_surface.lock()))
                    return;
                parent = parent->m_popup ? parent->m_popup->m_parent.lock() : nullptr;
            }
            originalPopupGrab(self, popup);
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
            originalStartDrag(self, source, icon, origin);
        }

        // A drag without a source stays within its client, as a private drag does. Hyprland's handler cannot take one, so
        // one from a window that is not driven, or is active, is ignored: there is no source to tell.
        void sourcelessDrag(const SP<CWLSurfaceResource>& origin, const SP<CWLSurfaceResource>& icon) {
            if (auto* session = protectedSession(origin))
                privateDrag(session, nullptr, icon);
            else
                Log::logger->log(Log::WARN, "hyprmasq", "ignoring a drag without a source");
        }

        // X11 has no popup grabs to refuse. An X11 app takes focus by asking for activation, or by mapping a window
        // that Hyprland focuses on map. Refuse both for apps with a driven window while the user is in another app.
        // xwayland-satellite windows all come from one process, and an XWayland window need not tell its process. For
        // activation, those count as one app per X server. New windows are only kept from focus by a known process, so
        // that apps the user starts still get focus.
        bool sameX11App(const PHLWINDOW& a, wl_client* aServer, const PHLWINDOW& b, wl_client* bServer, bool knownProcess) {
            const auto pid = [](const PHLWINDOW& w) { return w->backend().isX11() ? w->backend().pid() : 0; };
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
        // Activation requests of a window come only from XWayland.
        void activateX11(Desktop::View::CWindow* self) {
            if (drivenX11App(self->m_self.lock(), false)) {
                x11::refuseActivation(self->m_self.lock());
                return;
            }
            originalActivateX11(self);
        }
        // As for X11 apps, a Wayland app with a driven window cannot take the focus by asking for activation, which apps do
        // in answer to input, as a terminal does for its bell or a page through window.focus(): the driven window itself
        // never, and its app's other windows while the user is in another app. The app is the window's connection.
        bool drivenWaylandApp(const PHLWINDOW& w) {
            if (!w || w->backend().isX11() || !w->resource())
                return false;
            const auto* client  = w->resource()->client();
            const auto  focused = Desktop::focusState()->window();
            const bool  inApp   = focused && focused->resource() && focused->resource()->client() == client;
            return std::ranges::any_of(sessions, [&](const auto& entry) {
                const auto driven = entry.second->window.lock();
                return driven && !driven->backend().isX11() && driven->resource() && driven->resource()->client() == client && (driven == w || !inApp);
            });
        }
        // xwayland-satellite forwards activation requests as xdg-activation. A forced activation comes from the user,
        // for example through a taskbar. A window that the plugin placed silently, or of a Wayland app with a driven
        // window, is marked urgent but not focused, also with focus_on_activate, so that a popup put next to its driven
        // window cannot follow up by taking the focus, and the agent's input cannot move the user's focus or workspace.
        void activate(Desktop::View::CWindow* self, bool force) {
            if (!force && drivenX11App(self->m_self.lock(), false))
                return;
            auto&      suppression = activateFocusOnlyOf(*self);
            const bool suppressed  = suppression;
            if (!force && (launch::placed(self->m_self.lock()) || drivenWaylandApp(self->m_self.lock())))
                suppression = true;
            originalActivate(self, force);
            suppression = suppressed;
        }
        void windowFocus(Desktop::CFocusState* self, PHLWINDOW w, Desktop::eFocusReason reason, SP<CWLSurfaceResource> surface) {
            if (w && reason == Desktop::FOCUS_REASON_NEW_WINDOW && drivenX11App(w, true))
                return;
            originalWindowFocus(self, w, reason, surface);
        }

        // Every change to a window's position or size updates its decorations, the one place where moves show.
        void updateDecos(Desktop::View::CWindowPresentation* self) {
            originalUpdateDecos(self);
            for (auto& [_, session] : sessions) {
                if (auto w = session->window.lock(); w && &w->presentation() == self)
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
            originalKeyboardFocus(self, surface);
        }
        void setPointerFocus(CSeatManager* self, SP<CWLSurfaceResource> surface, const Vector2D& local) {
            if (surface)
                drag::cancelClient(surface->client(), "user_input");
            CKeptFocus<CWLPointerResource> kept(surface, &SSession::enteredPointer, &CWLSeatResource::m_pointers, &SSession::keepingPointer);
            originalPointerFocus(self, surface, local);
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
            originalKeyboardKey(self, timeMs, key, state);
        }
        void sendKeyboardMods(CSeatManager* self, uint32_t depressed, uint32_t latched, uint32_t locked, uint32_t group) {
            returnKeyboards(self->m_state.keyboardFocusResource);
            originalKeyboardMods(self, depressed, latched, locked, group);
        }
        void sendTouchDown(CSeatManager* self, SP<CWLSurfaceResource> surface, uint32_t timeMs, int32_t id, const Vector2D& local) {
            if (surface) {
                drag::cancelClient(surface->client(), "user_input");
                selection::userInput(surface->client());
            }
            originalTouchDown(self, surface, timeMs, id, local);
        }
        void sendPointerButton(CSeatManager* self, uint32_t timeMs, uint32_t button, wl_pointer_button_state state) {
            returnKeyboards(self->m_state.pointerFocusResource);
            originalPointerButton(self, timeMs, button, state);
        }
    }

    bool grabbing(const SP<CWLSurfaceResource>& surface) {
        // An app may give the surface of a popup that it closed another popup role, so the popup itself must live.
        return surface && std::ranges::any_of(grabs, [&](const WP<CXDGPopupResource>& p) {
                   const auto popup = p.lock();
                   const auto xdg   = popup ? popup->m_surface.lock() : nullptr;
                   return xdg && xdg->m_surface.lock() == surface;
               });
    }

    void initFocusPolicy(void* handle) {
        // Input to windows, see Session.hpp, and to X11 windows, whose apps ask for activation through XWayland and whose
        // new windows Hyprland focuses on map. CWindow::activate also keeps the windows that the plugin placed from
        // taking the focus, see Launch.hpp.
        auto& input = hooks::input;
        input.add(defineHook<PopupGrab, &popupGrab>(handle, "_ZN17CXDGShellProtocol14addOrStartGrabEN9Hyprutils6Memory14CSharedPointerI17CXDGPopupResourceEE",
                                                    "CXDGShellProtocol::addOrStartGrab(Hyprutils::Memory::CSharedPointer<CXDGPopupResource>)", originalPopupGrab));
        input.add(defineHook<StartDrag, &startDrag>(
            handle, "_ZN21CWLDataDeviceProtocol12initiateDragEN9Hyprutils6Memory12CWeakPointerI21CWLDataSourceResourceEENS1_14CSharedPointerI18CWLSurfaceResourceEES7_",
            "CWLDataDeviceProtocol::initiateDrag(Hyprutils::Memory::CWeakPointer<CWLDataSourceResource>, "
            "Hyprutils::Memory::CSharedPointer<CWLSurfaceResource>, Hyprutils::Memory::CSharedPointer<CWLSurfaceResource>)",
            originalStartDrag));
        auto& activateHook = defineHook<Activate, &activate>(handle, "_ZN7Desktop4View7CWindow8activateEb", "Desktop::View::CWindow::activate(bool)", originalActivate);
        input.add(activateHook);
        hooks::launch.add(activateHook);
        input.add(defineHook<UpdateDecorations, &updateDecos>(handle, "_ZN7Desktop4View19CWindowPresentation17updateDecorationsEv",
                                                              "Desktop::View::CWindowPresentation::updateDecorations()", originalUpdateDecos));
        input.add(defineHook<KeyboardFocus, &setKeyboardFocus>(handle, "_ZN12CSeatManager16setKeyboardFocusEN9Hyprutils6Memory14CSharedPointerI18CWLSurfaceResourceEE",
                                                               "CSeatManager::setKeyboardFocus(Hyprutils::Memory::CSharedPointer<CWLSurfaceResource>)", originalKeyboardFocus));
        input.add(defineHook<PointerFocus, &setPointerFocus>(
            handle, "_ZN12CSeatManager15setPointerFocusEN9Hyprutils6Memory14CSharedPointerI18CWLSurfaceResourceEERKNS0_4Math8Vector2DE",
            "CSeatManager::setPointerFocus(Hyprutils::Memory::CSharedPointer<CWLSurfaceResource>, Hyprutils::Math::Vector2D const&)", originalPointerFocus));
        input.add(defineHook<KeyboardKey, &sendKeyboardKey>(handle, "_ZN12CSeatManager15sendKeyboardKeyEjj21wl_keyboard_key_state",
                                                            "CSeatManager::sendKeyboardKey(unsigned int, unsigned int, wl_keyboard_key_state)", originalKeyboardKey));
        input.add(defineHook<KeyboardMods, &sendKeyboardMods>(handle, "_ZN12CSeatManager16sendKeyboardModsEjjjj",
                                                              "CSeatManager::sendKeyboardMods(unsigned int, unsigned int, unsigned int, unsigned int)", originalKeyboardMods));
        input.add(defineHook<PointerButton, &sendPointerButton>(handle, "_ZN12CSeatManager17sendPointerButtonEjj23wl_pointer_button_state",
                                                                "CSeatManager::sendPointerButton(unsigned int, unsigned int, wl_pointer_button_state)", originalPointerButton));
        // Also while an app's own copy is the agent clipboard, see Selection.cpp.
        auto& touchHook = defineHook<TouchDown, &sendTouchDown>(
            handle, "_ZN12CSeatManager13sendTouchDownEN9Hyprutils6Memory14CSharedPointerI18CWLSurfaceResourceEEjiRKNS0_4Math8Vector2DE",
            "CSeatManager::sendTouchDown(Hyprutils::Memory::CSharedPointer<CWLSurfaceResource>, unsigned int, int, Hyprutils::Math::Vector2D const&)", originalTouchDown);
        input.add(touchHook);
        hooks::clipboard.add(touchHook);
        auto& x11 = hooks::x11Input;
        x11.add(defineHook<ActivationRequest, &activateX11>(handle, "_ZN7Desktop4View7CWindow19onActivationRequestEv", "Desktop::View::CWindow::onActivationRequest()",
                                                            originalActivateX11));
        x11.add(defineHook<WindowFocus, &windowFocus>(
            handle, "_ZN7Desktop11CFocusState14rawWindowFocusEN9Hyprutils6Memory14CSharedPointerINS_4View7CWindowEEENS_12eFocusReasonENS3_I18CWLSurfaceResourceEE",
            "Desktop::CFocusState::rawWindowFocus(Hyprutils::Memory::CSharedPointer<Desktop::View::CWindow>, Desktop::eFocusReason, "
            "Hyprutils::Memory::CSharedPointer<CWLSurfaceResource>)",
            originalWindowFocus));
        drag::onSourceless(&sourcelessDrag);
        realKey = Event::bus()->m_events.input.keyboard.key.listen(
            [](const IKeyboard::SKeyEvent&, Event::SCallbackInfo&) { returnKeyboards(g_pSeatManager->m_state.keyboardFocusResource); });
    }

    void shutdownFocusPolicy() {
        realKey.reset();
        grabs.clear();
    }
}

template struct hyprmasq::SPinned<hyprmasq::input::PopupGrab, &CXDGShellProtocol::addOrStartGrab>;
template struct hyprmasq::SPinned<hyprmasq::input::StartDrag, &CWLDataDeviceProtocol::initiateDrag>;
template struct hyprmasq::SPinned<hyprmasq::input::ActivationRequest, &Desktop::View::CWindow::onActivationRequest>;
template struct hyprmasq::SPinned<hyprmasq::input::WindowFocus, &Desktop::CFocusState::rawWindowFocus>;
template struct hyprmasq::SPinned<hyprmasq::input::Activate, &Desktop::View::CWindow::activate>;
template struct hyprmasq::SPinned<hyprmasq::input::UpdateDecorations, &Desktop::View::CWindowPresentation::updateDecorations>;
template struct hyprmasq::SPinned<hyprmasq::input::KeyboardFocus, &CSeatManager::setKeyboardFocus>;
template struct hyprmasq::SPinned<hyprmasq::input::PointerFocus, &CSeatManager::setPointerFocus>;
template struct hyprmasq::SPinned<hyprmasq::input::KeyboardKey, &CSeatManager::sendKeyboardKey>;
template struct hyprmasq::SPinned<hyprmasq::input::KeyboardMods, &CSeatManager::sendKeyboardMods>;
template struct hyprmasq::SPinned<hyprmasq::input::PointerButton, &CSeatManager::sendPointerButton>;
template struct hyprmasq::SPinned<hyprmasq::input::TouchDown, &CSeatManager::sendTouchDown>;
