#include "X11.hpp"
#include "Hooks.hpp"
#include <hyprland/src/desktop/state/FocusState.hpp>
#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/helpers/time/Time.hpp>
#include <hyprland/src/managers/SeatManager.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#include <hyprland/src/xwayland/Server.hpp>
#include <hyprland/src/xwayland/XSurface.hpp>
#include <hyprland/src/xwayland/XWayland.hpp>
#include <linux/sockios.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <wayland-server-core.h>
#include <xcb/xcb.h>
#include <xcb/xcbext.h>
#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <format>
#include <map>
#include <memory>
#include <ranges>
#include <thread>

class CXWM;

namespace hyprmasq::x11 {
    namespace {
        struct SFree {
            void operator()(void* p) const {
                free(p);
            }
        };
        template <typename T>
        using Reply = std::unique_ptr<T, SFree>;

        struct SFocus {
            xcb_window_t window, previous;
            uint8_t      revertTo;
        };

        xcb_connection_t*     connection   = nullptr;
        xcb_atom_t            activeWindow = XCB_ATOM_NONE; // _NET_ACTIVE_WINDOW on this connection
        std::optional<SFocus> held;
        Time::steady_tp       ignoreFocusInUntil = Time::steadyNow();
        CHyprSignalListener   keyboardFocus;
        void*                 originalActivateSurface = nullptr;
        void*                 originalHandleFocusIn   = nullptr;

        // A connection of our own, so round trips never consume the window manager's events.
        xcb_connection_t* x() {
            if (connection && xcb_connection_has_error(connection)) {
                xcb_disconnect(connection);
                connection = nullptr;
                held.reset(); // XWayland restarted, its state is gone
                activeWindow = XCB_ATOM_NONE;
            }
            // Hyprland exports DISPLAY when XWayland is ready, and has a window manager from then on.
            const char* display = getenv("DISPLAY");
            if (!connection && g_pXWayland && g_pXWayland->m_wm && display) {
                connection = xcb_connect(display, nullptr);
                if (xcb_connection_has_error(connection)) {
                    xcb_disconnect(connection);
                    connection = nullptr;
                }
            }
            // Selects no events, so only errors of unchecked requests queue up here.
            while (connection) {
                auto* event = xcb_poll_for_event(connection);
                if (!event)
                    break;
                free(event);
            }
            return connection;
        }

        // Like xcb_wait_for_reply, but gives up after 100 ms, so a stuck X server cannot freeze the compositor.
        template <typename T>
        Reply<T> reply(xcb_connection_t* c, unsigned int sequence) {
            xcb_flush(c);
            const auto deadline = Time::steadyNow() + std::chrono::milliseconds(100);
            while (true) {
                void*                result = nullptr;
                xcb_generic_error_t* error  = nullptr;
                if (xcb_poll_for_reply(c, sequence, &result, &error)) {
                    free(error);
                    return Reply<T>{static_cast<T*>(result)};
                }
                const auto left = std::chrono::ceil<std::chrono::milliseconds>(deadline - Time::steadyNow()).count();
                if (xcb_connection_has_error(c) || left <= 0) {
                    xcb_discard_reply(c, sequence);
                    return nullptr;
                }
                pollfd fd{.fd = xcb_get_file_descriptor(c), .events = POLLIN, .revents = 0};
                ::poll(&fd, 1, left);
            }
        }

        Reply<xcb_get_input_focus_reply_t> inputFocus(xcb_connection_t* c) {
            return reply<xcb_get_input_focus_reply_t>(c, xcb_get_input_focus(c).sequence);
        }

        // Writes _NET_ACTIVE_WINDOW back unchanged. Every write notifies X11 apps that track the active window, and Wine
        // only gives up the foreground, which X focus from emulated input or a click gives it, when that property names
        // a window other than its own.
        void announceActiveWindow(xcb_connection_t* c) {
            if (activeWindow == XCB_ATOM_NONE) {
                constexpr std::string_view NAME = "_NET_ACTIVE_WINDOW";
                auto                       atom = reply<xcb_intern_atom_reply_t>(c, xcb_intern_atom(c, false, NAME.size(), NAME.data()).sequence);
                if (!atom)
                    return;
                activeWindow = atom->atom;
            }
            const auto root  = xcb_setup_roots_iterator(xcb_get_setup(c)).data->root;
            auto       value = reply<xcb_get_property_reply_t>(c, xcb_get_property(c, false, root, activeWindow, XCB_GET_PROPERTY_TYPE_ANY, 0, 1).sequence);
            if (!value || value->format != 32 || value->bytes_after != 0 || xcb_get_property_value_length(value.get()) != 4)
                return;
            xcb_change_property(c, XCB_PROP_MODE_REPLACE, root, activeWindow, value->type, 32, 1, xcb_get_property_value(value.get()));
            xcb_flush(c);
        }

        // Waits until XWayland has read everything sent to it over Wayland. XWayland queues input as it reads and
        // processes queued input before any further X request, so later focus and stacking changes cannot overtake it.
        // False if XWayland has not read it all within 100 ms.
        bool drain() {
            auto* client = g_pXWayland && g_pXWayland->m_server ? g_pXWayland->m_server->m_xwaylandClient : nullptr;
            if (!client)
                return true;
            wl_client_flush(client);
            const int  fd       = wl_client_get_fd(client);
            const auto deadline = Time::steadyNow() + std::chrono::milliseconds(100);
            int        unread   = 0;
            while (ioctl(fd, SIOCOUTQ, &unread) == 0 && unread > 0) {
                if (Time::steadyNow() >= deadline)
                    return false;
                std::this_thread::sleep_for(std::chrono::microseconds(100));
            }
            return true;
        }

        std::optional<std::vector<xcb_window_t>> stackingOrder(xcb_connection_t* c) {
            auto tree = reply<xcb_query_tree_reply_t>(c, xcb_query_tree(c, xcb_setup_roots_iterator(xcb_get_setup(c)).data->root).sequence);
            if (!tree)
                return std::nullopt;
            const auto* children = xcb_query_tree_children(tree.get());
            return std::vector<xcb_window_t>{children, children + xcb_query_tree_children_length(tree.get())};
        }

        std::map<xcb_window_t, SP<CXWaylandSurface>> mappedSurfaces() {
            std::map<xcb_window_t, SP<CXWaylandSurface>> result;
            for (const auto& w : Desktop::windowState()->windows()) {
                if (auto s = w && w->m_isX11 && w->m_isMapped ? w->m_xwaylandSurface.lock() : nullptr)
                    result.emplace(s->m_xID, s);
            }
            return result;
        }

        // Puts the windows on top, bottom to top. The window manager redirects restacking of managed windows by other
        // clients to itself, so this goes through Hyprland's, and waits until the X server shows the result.
        bool restack(xcb_connection_t* c, const std::vector<xcb_window_t>& windows) {
            const auto                surfaces = mappedSurfaces();
            std::vector<xcb_window_t> moved;
            for (auto id : windows) {
                if (surfaces.contains(id)) {
                    surfaces.at(id)->restackToTop();
                    moved.push_back(id);
                }
            }
            const auto deadline = Time::steadyNow() + std::chrono::milliseconds(100);
            while (true) {
                auto order = stackingOrder(c);
                if (order && order->size() >= moved.size() && std::equal(moved.begin(), moved.end(), order->end() - moved.size()))
                    return true;
                if (!order || Time::steadyNow() >= deadline)
                    return false;
                std::this_thread::sleep_for(std::chrono::microseconds(200));
            }
        }

        void activateSurface(CXWM* self, SP<CXWaylandSurface> surface, bool activate) {
            // Hyprland is about to move X focus for the user: emulated keys land first, then the focus goes back.
            drain();
            release();
            using Fn = void (*)(CXWM*, SP<CXWaylandSurface>, bool);
            reinterpret_cast<Fn>(originalActivateSurface)(self, surface, activate);
        }

        void handleFocusIn(CXWM* self, xcb_focus_in_event_t* event) {
            // The window manager adopts focus changes it did not make when they stay within one app. Ours are temporary.
            if (held || Time::steadyNow() < ignoreFocusInUntil)
                return;
            using Fn = void (*)(CXWM*, xcb_focus_in_event_t*);
            reinterpret_cast<Fn>(originalHandleFocusIn)(self, event);
        }
    }

    void init(void* handle) {
        originalActivateSurface = hook(handle, "CXWM::activateSurface", reinterpret_cast<void*>(&activateSurface));
        originalHandleFocusIn   = hook(handle, "CXWM::handleFocusIn", reinterpret_cast<void*>(&handleFocusIn));
        // Keyboard focus can reach an XWayland surface without the window manager, for example on an unmanaged window.
        keyboardFocus = g_pSeatManager->m_events.keyboardFocusChange.listen([] { release(); });
    }

    void shutdown() {
        release();
        keyboardFocus.reset();
        if (connection)
            xcb_disconnect(connection);
        connection = nullptr;
    }

    wl_client* server(const PHLWINDOW& window) {
        auto resource = window ? window->resource() : nullptr;
        if (!resource)
            return nullptr;
        auto* client = resource->client();
        if (window->m_isX11)
            return client;
        pid_t pid = 0;
        wl_client_get_credentials(client, &pid, nullptr, nullptr);
        std::error_code error;
        const auto      exe = std::filesystem::read_symlink(std::format("/proc/{}/exe", pid), error);
        // Nix wraps it as .xwayland-satellite-wrapped.
        return !error && exe.filename().string().contains("xwayland-satellite") ? client : nullptr;
    }

    bool focus(const PHLWINDOW& window) {
        auto surface = window->m_xwaylandSurface.lock();
        if (!surface)
            return false;
        // The user is in this window, so X focus is wherever the app put it.
        if (Desktop::focusState()->window() == window)
            return true;
        if (held && held->window == surface->m_xID)
            return true;
        release();
        auto* c = x();
        if (!c || !drain())
            return false;
        auto current = inputFocus(c);
        if (!current)
            return false;
        if (current->focus == surface->m_xID)
            return true;
        held = SFocus{.window = surface->m_xID, .previous = current->focus, .revertTo = current->revert_to};
        xcb_set_input_focus(c, XCB_INPUT_FOCUS_POINTER_ROOT, surface->m_xID, XCB_CURRENT_TIME);
        // The round trip makes the focus change land before the keys, which travel over Wayland.
        auto after = inputFocus(c);
        if (!after || after->focus != surface->m_xID) {
            release();
            return false;
        }
        return true;
    }

    void release(const PHLWINDOW& window) {
        if (!held)
            return;
        if (window) {
            auto surface = window->m_xwaylandSurface.lock();
            if (!surface || surface->m_xID != held->window)
                return;
        }
        const auto focus = *held;
        held.reset();
        ignoreFocusInUntil = Time::steadyNow() + std::chrono::milliseconds(500);
        auto* c            = x();
        if (!c)
            return;
        drain();
        // Nothing else may move focus between the check and the restore.
        xcb_grab_server(c);
        if (auto current = inputFocus(c); current && current->focus == focus.window) {
            xcb_set_input_focus(c, focus.revertTo, focus.previous, XCB_CURRENT_TIME);
            // Focus stays put if the previous window is gone or no longer viewable.
            if (auto after = inputFocus(c); after && after->focus == focus.window)
                xcb_set_input_focus(c, XCB_INPUT_FOCUS_POINTER_ROOT, XCB_NONE, XCB_CURRENT_TIME);
        }
        xcb_ungrab_server(c);
        inputFocus(c);
        announceActiveWindow(c);
    }

    void refuseActivation(const PHLWINDOW& window) {
        auto surface = window ? window->m_xwaylandSurface.lock() : nullptr;
        // While X focus is held for it, the app has to stay active to receive the keys. release() tells it later.
        if (!surface || (held && held->window == surface->m_xID))
            return;
        if (auto* c = x())
            announceActiveWindow(c);
    }

    std::optional<SStack> raise(const PHLWINDOW& window) {
        auto surface = window->m_xwaylandSurface.lock();
        auto c       = surface ? x() : nullptr;
        auto before  = c ? stackingOrder(c) : std::nullopt;
        if (!before || !std::ranges::contains(*before, surface->m_xID))
            return std::nullopt;
        SStack stack{.before = *before, .raised = {}};

        // Menus are separate override-redirect windows and have to stay above the window that opened them.
        const auto mask     = xcb_get_setup(c)->resource_id_mask;
        const auto surfaces = mappedSurfaces();
        for (auto id : stack.before) {
            auto s = surfaces.contains(id) ? surfaces.at(id) : nullptr;
            if (id == surface->m_xID || (s && s->m_overrideRedirect && (id & ~mask) == (surface->m_xID & ~mask)))
                stack.raised.push_back(id);
        }
        if (std::equal(stack.raised.begin(), stack.raised.end(), stack.before.end() - stack.raised.size())) {
            stack.raised.clear(); // already on top
            return stack;
        }

        if (!drain())
            return std::nullopt;
        if (!restack(c, stack.raised)) {
            lower(stack);
            return std::nullopt;
        }
        return stack;
    }

    void lower(const SStack& stack) {
        auto* c = stack.raised.empty() ? nullptr : x();
        if (!c)
            return;
        // Everything that was above the lowest raised window goes back on top of it, in its old order.
        const auto lowest = std::ranges::find_first_of(stack.before, stack.raised);
        drain();
        restack(c, std::vector<xcb_window_t>(lowest + 1, stack.before.end()));
    }
}
