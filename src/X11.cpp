#include "X11.hpp"
#include "Eis.hpp"
#include "Hooks.hpp"
#include "PrivateAccess.hpp"
#include <hyprland/src/desktop/state/FocusState.hpp>
#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/desktop/view/window/Window.hpp>
#include <hyprland/src/desktop/view/window/X11Backend.hpp>
#include <hyprland/src/helpers/time/Time.hpp>
#include <hyprland/src/managers/SeatManager.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#include <hyprland/src/xwayland/Server.hpp>
#include <hyprland/src/xwayland/XSurface.hpp>
#include <hyprland/src/xwayland/XWM.hpp>
#include <hyprland/src/xwayland/XWayland.hpp>
#include <linux/sockios.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <wayland-server-core.h>
#include <xcb/xcb.h>
#include <xcb/xcbext.h>
#include <algorithm>
#include <array>
#include <charconv>
#include <cstdlib>
#include <filesystem>
#include <format>
#include <functional>
#include <future>
#include <map>
#include <memory>
#include <ranges>
#include <span>
#include <string>
#include <system_error>
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

        // What the main thread waits for the X server, or for XWayland to read, within a CWaitBudget: WAIT_BUDGET in
        // all, so that an X server that does not answer holds the compositor up that long at most, however many round
        // trips an input frame makes. Once WAIT_BUDGET has run out, SHORT_BUDGET for SHORT_FOR, or until a round trip
        // answers in time, so that a stuck X server costs little more, and a slow one gets WAIT_BUDGET again.
        constexpr auto WAIT_BUDGET  = std::chrono::milliseconds(100);
        constexpr auto SHORT_BUDGET = std::chrono::milliseconds(10);
        constexpr auto SHORT_FOR    = std::chrono::seconds(1);
        // How often a wait within the budget looks again whether XWayland has read everything, see drain(), and whether
        // the X server shows a restack, see restack().
        constexpr auto DRAIN_POLL   = std::chrono::microseconds(100);
        constexpr auto RESTACK_POLL = std::chrono::microseconds(200);
        // How long after release() the window manager ignores focus-in events, which the restored X focus causes, see
        // handleFocusIn().
        constexpr auto                 FOCUS_IN_GRACE = std::chrono::milliseconds(500);

        int                            budgetDepth = 0;
        Time::steady_tp                budgetEnd;
        bool                           shortBudget = false; // the budget that budgetEnd ends
        std::optional<Time::steady_tp> shortUntil;

        // A wait ran out of the budget.
        void late() {
            if (!shortBudget)
                shortUntil = Time::steadyNow() + SHORT_FOR;
        }

        // A connection that is being set up. xcb_connect waits for the X server's answer without a limit, so the
        // handshake runs on a thread of its own, on a duplicate of fd: shutdown() on fd ends a handshake that waits.
        struct SConnecting {
            int                            fd = -1;
            std::thread                    thread;
            std::future<xcb_connection_t*> result;
        };
        std::optional<SConnecting> connecting;

        // A socket connected to the X server of a DISPLAY of the form :N or :N.S, which Hyprland exports for its
        // XWayland. Hyprland's XWayland listens on /tmp/.X11-unix/XN, also where it listens on an abstract socket.
        int connectDisplay(std::string_view display) {
            if (!display.starts_with(':'))
                return -1;
            display.remove_prefix(1);
            display                = display.substr(0, display.find('.'));
            unsigned int number    = 0;
            const auto [end, code] = std::from_chars(display.data(), display.data() + display.size(), number);
            if (display.empty() || code != std::errc{} || end != display.data() + display.size())
                return -1;
            sockaddr_un addr{.sun_family = AF_UNIX, .sun_path = {}};
            const auto  path = std::format("/tmp/.X11-unix/X{}", number);
            if (path.size() >= sizeof(addr.sun_path))
                return -1;
            std::ranges::copy(path, addr.sun_path);
            // Without waiting for room in the backlog of a server that does not accept.
            const int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
            if (fd >= 0 && ::connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
                close(fd);
                return -1;
            }
            return fd;
        }

        void startConnect() {
            const char* display = getenv("DISPLAY");
            const int   fd      = display ? connectDisplay(display) : -1;
            const int   theirs  = fd < 0 ? -1 : fcntl(fd, F_DUPFD_CLOEXEC, 0);
            if (theirs < 0) {
                if (fd >= 0)
                    close(fd);
                return;
            }
            std::promise<xcb_connection_t*> promise;
            auto                            result = promise.get_future();
            try {
                // Hyprland's XWayland takes no authorization.
                std::thread thread([theirs, promise = std::move(promise)] mutable {
                    pthread_setname_np(pthread_self(), "hyprmasq-x11");
                    promise.set_value(xcb_connect_to_fd(theirs, nullptr));
                });
                connecting = SConnecting{.fd = fd, .thread = std::move(thread), .result = std::move(result)};
            } catch (const std::system_error&) {
                close(theirs);
                close(fd);
            }
        }

        // Takes the connection once its handshake has ended, waiting for that until the budget runs out.
        void finishConnect() {
            if (!connecting)
                return;
            if (connecting->result.wait_until(budgetEnd) != std::future_status::ready) {
                late();
                return;
            }
            connection = connecting->result.get();
            connecting->thread.join();
            close(connecting->fd);
            connecting.reset();
            if (xcb_connection_has_error(connection)) {
                xcb_disconnect(connection);
                connection = nullptr;
            }
        }

        // A connection of our own, so round trips never consume the window manager's events.
        xcb_connection_t* x() {
            CWaitBudget budget;
            if (connection && xcb_connection_has_error(connection)) {
                xcb_disconnect(connection);
                connection = nullptr;
                held.reset(); // XWayland restarted, its state is gone
                activeWindow = XCB_ATOM_NONE;
            }
            // Hyprland exports DISPLAY when XWayland is ready, and has a window manager from then on.
            if (!connection && g_pXWayland && g_pXWayland->m_wm) {
                if (!connecting)
                    startConnect();
                finishConnect();
            }
            // Selects no events, so only errors of unchecked requests queue up here.
            while (connection) {
                auto* event = xcb_poll_for_event(connection);
                if (!event)
                    break;
                free(event);
            }
            // A write waits for room in the socket, which an X server that does not read leaves full. No request goes
            // out while what it has not read fills half the socket's buffer, more than the requests of many inputs.
            int queued = 0, buffer = 0;
            if (socklen_t size = sizeof(buffer); connection && ioctl(xcb_get_file_descriptor(connection), SIOCOUTQ, &queued) == 0 &&
                getsockopt(xcb_get_file_descriptor(connection), SOL_SOCKET, SO_SNDBUF, &buffer, &size) == 0 && queued > buffer / 2) {
                late();
                return nullptr;
            }
            return connection;
        }

        // Like xcb_wait_for_reply, but gives up once the budget has run out, so a stuck X server cannot freeze the
        // compositor.
        template <typename T>
        Reply<T> reply(xcb_connection_t* c, unsigned int sequence) {
            CWaitBudget budget;
            xcb_flush(c);
            while (true) {
                void*                result = nullptr;
                xcb_generic_error_t* error  = nullptr;
                if (xcb_poll_for_reply(c, sequence, &result, &error)) {
                    free(error);
                    shortUntil.reset();
                    return Reply<T>{static_cast<T*>(result)};
                }
                const auto left = std::chrono::ceil<std::chrono::milliseconds>(budgetEnd - Time::steadyNow()).count();
                if (xcb_connection_has_error(c) || left <= 0) {
                    if (left <= 0)
                        late();
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
        // False if XWayland has not read it all before the budget runs out.
        bool drain() {
            auto* client = xwaylandClient();
            if (!client)
                return true;
            CWaitBudget budget;
            wl_client_flush(client);
            const int fd     = wl_client_get_fd(client);
            int       unread = 0;
            while (ioctl(fd, SIOCOUTQ, &unread) == 0 && unread > 0) {
                if (Time::steadyNow() >= budgetEnd) {
                    late();
                    return false;
                }
                std::this_thread::sleep_for(DRAIN_POLL);
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

        // The X window of an XWayland window, while its XWayland surface lives.
        std::optional<xcb_window_t> xWindow(const PHLWINDOW& w) {
            if (!w || !w->backend().isX11() || !w->backend().valid())
                return std::nullopt;
            return sc<xcb_window_t>(w->backend().clientID().id);
        }

        // The XWayland surface of an XWayland window, while it lives.
        SP<CXWaylandSurface> xSurface(const PHLWINDOW& w) {
            if (!w || !w->backend().isX11() || !w->backend().valid())
                return nullptr;
            return xwaylandSurfaceOf(sc<Desktop::View::CX11Backend&>(w->backend())).lock();
        }

        std::map<xcb_window_t, SP<CXWaylandSurface>> mappedSurfaces() {
            std::map<xcb_window_t, SP<CXWaylandSurface>> result;
            for (const auto& w : Desktop::windowState()->windows()) {
                if (auto s = w && w->mapped() ? xSurface(w) : nullptr)
                    result.emplace(s->m_xID, s);
            }
            return result;
        }

        // Whether two windows belong to the same X client: the X server gives each client a range of ids of its own.
        bool sameClient(xcb_connection_t* c, xcb_window_t a, xcb_window_t b) {
            const auto mask = xcb_get_setup(c)->resource_id_mask;
            return (a & ~mask) == (b & ~mask);
        }

        // A menu of the app of window: one of its X client's override-redirect windows, which X11 toolkits open their
        // menus and lists in.
        bool menuOf(xcb_connection_t* c, xcb_window_t window, const CXWaylandSurface& other) {
            return other.m_overrideRedirect && sameClient(c, window, other.m_xID);
        }

        // Hyprland's window manager's connection. The window manager redirects the restacking of managed windows by
        // other clients to itself, and has no function that stacks a window next to another.
        xcb_connection_t* windowManager() {
            return g_pXWayland && g_pXWayland->m_wm ? connectionOf(*g_pXWayland->m_wm) : nullptr;
        }

        // Waits until the X server shows a stacking order that done accepts, as long as the budget lasts.
        bool waitForStack(xcb_connection_t* c, const std::function<bool(const std::vector<xcb_window_t>&)>& done) {
            CWaitBudget budget;
            while (true) {
                auto order = stackingOrder(c);
                if (order && done(*order))
                    return true;
                if (!order || Time::steadyNow() >= budgetEnd)
                    return false;
                std::this_thread::sleep_for(RESTACK_POLL);
            }
        }

        // The index of a window in a stacking order, or nullopt.
        std::optional<size_t> indexOf(const std::vector<xcb_window_t>& order, xcb_window_t window) {
            const auto it = std::ranges::find(order, window);
            return it == order.end() ? std::nullopt : std::optional{sc<size_t>(it - order.begin())};
        }

        void activateSurface(CXWM* self, SP<CXWaylandSurface> surface, bool activate) {
            // Hyprland is about to move X focus for the user: emulated keys land first, then the focus goes back. Only
            // a driven window of this X server can have emulated input on its way.
            if (held || drivesClient(xwaylandClient())) {
                CWaitBudget budget;
                drain();
                release();
            }
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

    CWaitBudget::CWaitBudget() {
        if (budgetDepth++ > 0)
            return;
        const auto now = Time::steadyNow();
        shortBudget    = shortUntil && now < *shortUntil;
        budgetEnd      = now + (shortBudget ? SHORT_BUDGET : WAIT_BUDGET);
    }

    CWaitBudget::~CWaitBudget() {
        --budgetDepth;
    }

    void init(void* handle) {
        originalActivateSurface = hook(handle, "CXWM::activateSurface", reinterpret_cast<void*>(&activateSurface));
        originalHandleFocusIn   = hook(handle, "CXWM::handleFocusIn", reinterpret_cast<void*>(&handleFocusIn));
        // Keyboard focus can reach an XWayland surface without the window manager, for example on an unmanaged window.
        // Elsewhere, X focus routes none of the user's keys, and an X11 app closes its menus once it loses it.
        keyboardFocus = g_pSeatManager->m_events.keyboardFocusChange.listen([] {
            const auto focused = g_pSeatManager->m_state.keyboardFocus.lock();
            if (focused && focused->client() == xwaylandClient())
                release();
        });
    }

    void shutdown() {
        release();
        keyboardFocus.reset();
        if (connecting) {
            // Ends a handshake that waits for the X server.
            ::shutdown(connecting->fd, SHUT_RDWR);
            connecting->thread.join();
            if (auto* c = connecting->result.get())
                xcb_disconnect(c);
            close(connecting->fd);
            connecting.reset();
        }
        if (connection)
            xcb_disconnect(connection);
        connection   = nullptr;
        activeWindow = XCB_ATOM_NONE;
        held.reset();
        shortUntil.reset();
    }

    wl_client* xwaylandClient() {
        return g_pXWayland && g_pXWayland->m_server ? g_pXWayland->m_server->m_xwaylandClient : nullptr;
    }

    wl_client* server(const PHLWINDOW& window) {
        auto resource = window ? window->resource() : nullptr;
        if (!resource)
            return nullptr;
        auto* client = resource->client();
        return window->backend().isX11() || serves(client) ? client : nullptr;
    }

    bool serves(wl_client* client) {
        if (!client)
            return false;
        if (client == xwaylandClient())
            return true;
        pid_t pid = 0;
        wl_client_get_credentials(client, &pid, nullptr, nullptr);
        std::error_code error;
        const auto      exe = std::filesystem::read_symlink(std::format("/proc/{}/exe", pid), error);
        // Nix wraps it as .xwayland-satellite-wrapped.
        return !error && exe.filename().string().contains("xwayland-satellite");
    }

    bool focus(const PHLWINDOW& window) {
        const auto id = xWindow(window);
        if (!id)
            return false;
        // The user is in this window, so X focus is wherever the app put it.
        if (Desktop::focusState()->window() == window)
            return true;
        if (held && held->window == *id)
            return true;
        CWaitBudget budget;
        // A menu of the app whose window holds it gets its keys through that window: X focus never goes to a menu, and
        // Chromium closes its menus once its window loses the X focus.
        if (const auto surface = xSurface(window); surface && held && x() && menuOf(x(), held->window, *surface))
            return true;
        release();
        auto* c = x();
        if (!c || !drain())
            return false;
        auto current = inputFocus(c);
        if (!current)
            return false;
        if (current->focus == *id)
            return true;
        held = SFocus{.window = *id, .previous = current->focus, .revertTo = current->revert_to};
        xcb_set_input_focus(c, XCB_INPUT_FOCUS_POINTER_ROOT, *id, XCB_CURRENT_TIME);
        // The round trip makes the focus change land before the keys, which travel over Wayland.
        auto after = inputFocus(c);
        if (!after || after->focus != *id) {
            release();
            return false;
        }
        return true;
    }

    void release(const PHLWINDOW& window) {
        if (!held)
            return;
        if (window) {
            const auto id = xWindow(window);
            if (!id || *id != held->window)
                return;
        }
        const auto  focus = *held;
        CWaitBudget budget;
        held.reset();
        ignoreFocusInUntil = Time::steadyNow() + FOCUS_IN_GRACE;
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
        const auto id = xWindow(window);
        // While X focus is held, the app of the window holding it has to stay active to take the input, and an app can
        // ask to activate another of its windows. release() tells them later.
        if (!id || held)
            return;
        CWaitBudget budget;
        if (auto* c = x())
            announceActiveWindow(c);
    }

    std::optional<SStack> raise(const PHLWINDOW& window) {
        CWaitBudget budget;
        auto        surface = xSurface(window);
        auto        c       = surface ? x() : nullptr;
        auto        before  = c ? stackingOrder(c) : std::nullopt;
        const auto  at      = before ? indexOf(*before, surface->m_xID) : std::nullopt;
        if (!at)
            return std::nullopt;
        const xcb_window_t target = surface->m_xID;
        SStack             stack{.before = *before, .lowered = {}};

        // The windows above the target go below it, but for its app's menus, which stay above the window that opened
        // them. Those stay where they are: a window that moves across the pointer sends the app crossing events, which
        // GTK takes for a pointer that moved into its menu, and its next release then picks the item under it.
        const auto surfaces = mappedSurfaces();
        for (auto id : std::span(stack.before).subspan(*at + 1))
            if (surfaces.contains(id) && !menuOf(c, target, *surfaces.at(id)))
                stack.lowered.push_back(id);
        if (stack.lowered.empty())
            return stack;

        auto* wm = windowManager();
        if (!wm || !drain())
            return std::nullopt;
        // Bottom to top, each right below the target, so that they keep their order.
        for (auto id : stack.lowered) {
            const uint32_t values[] = {target, XCB_STACK_MODE_BELOW};
            xcb_configure_window(wm, id, XCB_CONFIG_WINDOW_SIBLING | XCB_CONFIG_WINDOW_STACK_MODE, values);
        }
        xcb_flush(wm);
        const bool lowered = waitForStack(c, [&](const std::vector<xcb_window_t>& order) {
            const auto above = indexOf(order, target);
            return above && std::ranges::none_of(stack.lowered, [&](xcb_window_t id) {
                       const auto i = indexOf(order, id);
                       return i && *i > *above;
                   });
        });
        if (!lowered) {
            lower(stack);
            return std::nullopt;
        }
        return stack;
    }

    void lower(const SStack& stack) {
        CWaitBudget budget;
        auto*       c  = stack.lowered.empty() ? nullptr : x();
        auto*       wm = c ? windowManager() : nullptr;
        if (!wm)
            return;
        drain();
        // Once the budget has run out, as after a raise() that the X server did not answer in time, the windows go back
        // without the current order, as if all of them still existed.
        const auto current = stackingOrder(c);
        const auto exists  = [&](xcb_window_t id) { return !current || indexOf(*current, id).has_value(); };
        // Each window goes back right above the one that it was above, or the next one below that, which still exist,
        // bottom to top. Windows that opened meanwhile, such as the menu that a click opened, stay on top.
        std::vector<std::pair<xcb_window_t, xcb_window_t>> placed;
        for (auto id : stack.lowered) {
            const auto at = indexOf(stack.before, id);
            if (!at || !exists(id))
                continue;
            std::optional<xcb_window_t> below;
            for (auto i = *at; !below && i-- > 0;)
                if (exists(stack.before[i]))
                    below = stack.before[i];
            if (!below)
                continue;
            const uint32_t values[] = {*below, XCB_STACK_MODE_ABOVE};
            xcb_configure_window(wm, id, XCB_CONFIG_WINDOW_SIBLING | XCB_CONFIG_WINDOW_STACK_MODE, values);
            placed.emplace_back(id, *below);
        }
        xcb_flush(wm);
        if (!current)
            return;
        waitForStack(c, [&](const std::vector<xcb_window_t>& order) {
            return std::ranges::all_of(placed, [&](const auto& pair) {
                const auto window = indexOf(order, pair.first), sibling = indexOf(order, pair.second);
                return !window || !sibling || *window > *sibling;
            });
        });
    }

    bool keepsFocus(const PHLWINDOW& window) {
        auto surface = xSurface(window);
        if (!surface || !held || held->window != surface->m_xID)
            return false;
        CWaitBudget budget;
        auto*       c = x();
        if (!c || !held)
            return false;
        static const std::array<std::string, 4> MENUS      = {"_NET_WM_WINDOW_TYPE_COMBO", "_NET_WM_WINDOW_TYPE_DROPDOWN_MENU", "_NET_WM_WINDOW_TYPE_MENU",
                                                              "_NET_WM_WINDOW_TYPE_POPUP_MENU"};
        const auto                              isMenuType = [](uint32_t atom) { return std::ranges::any_of(MENUS, [&](const auto& name) { return HYPRATOMS[name] == atom; }); };
        return std::ranges::any_of(mappedSurfaces(),
                                   [&](const auto& entry) { return menuOf(c, held->window, *entry.second) && std::ranges::any_of(entry.second->m_atoms, isMenuType); });
    }

    std::optional<SWindowKind> kind(const PHLWINDOW& window) {
        auto surface = xSurface(window);
        if (!surface)
            return std::nullopt;
        // Without a round trip, as the window manager last read WM_PROTOCOLS: when the window got its surface, and each
        // time the app set it since. It keeps the list when the app deletes the property, which apps do not do.
        return SWindowKind{.overrideRedirect = surface->m_overrideRedirect, .deleteWindow = std::ranges::contains(surface->m_protocols, HYPRATOMS["WM_DELETE_WINDOW"])};
    }
}
