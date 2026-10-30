#include "Eis.hpp"
#include "FocusPolicy.hpp"
#include "Session.hpp"
#include "Wait.hpp"
#include "X11.hpp"
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/desktop/state/FocusState.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/devices/IKeyboard.hpp>
#include <hyprland/src/event/EventBus.hpp>
#include <hyprland/src/managers/SeatManager.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#include <hyprland/src/render/Renderer.hpp>
#include <libeis.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <fcntl.h>
#include <unistd.h>
#include <algorithm>
#include <cerrno>
#include <format>
#include <map>
#include <memory>
#include <random>
#include <ranges>

namespace hyprmasq::input {
    std::map<std::string, std::unique_ptr<SSession>> sessions;

    namespace {
        CHyprSignalListener monitorLayout;

        // Socket ids: random for each load of the plugin, then counting.
        uint64_t socketNonce = 0, socketCount = 0;

        void closeClosedWindow(const std::string& address) {
            if (const auto it = sessions.find(address); it != sessions.end()) {
                it->second->closing = "window_closed";
                it->second->answerPings();
                sessions.erase(it);
            }
        }

        SSession* sessionOf(const PHLWINDOW& window) {
            for (const auto& [_, session] : sessions)
                if (session->window.lock() == window)
                    return session.get();
            return nullptr;
        }

        bool x11Window(const PHLWINDOW& window, SSession* session) {
            return session ? session->xServer() != nullptr : x11::server(window) != nullptr;
        }

        // Refuse symlinks and pre-existing directories accessible to another user. made tells whether this created it.
        int privateDir(int parent, const char* name, bool& made) {
            made = mkdirat(parent, name, 0700) == 0;
            if (!made && errno != EEXIST)
                return -1;
            int         fd = openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            struct stat st{};
            if (fd >= 0 && (fstat(fd, &st) < 0 || st.st_uid != getuid() || (st.st_mode & 0777) != 0700)) {
                close(fd);
                return -1;
            }
            return fd;
        }

        // The directories of the sockets that openEis created since the load: $XDG_RUNTIME_DIR/hyprmasq, and the
        // instance's directory in it.
        struct SMadeDirectories {
            std::string runtime, instance;
            bool        root = false, own = false;
        } made;

        // Removes the directories that openEis created, once the sockets in them are gone, each only while it is empty:
        // hyprmasq-mcp keeps its locks and shots in the instance's directory, and other instances have theirs beside it.
        void removeDirectories() {
            const auto dirs = std::exchange(made, {});
            if (!dirs.root && !dirs.own)
                return;
            const int base = open(dirs.runtime.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            const int root = base < 0 ? -1 : openat(base, "hyprmasq", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            // unlinkat removes only an empty directory, and no symlink, with AT_REMOVEDIR.
            if (root >= 0 && dirs.own)
                unlinkat(root, dirs.instance.c_str(), AT_REMOVEDIR);
            if (base >= 0 && dirs.root)
                unlinkat(base, "hyprmasq", AT_REMOVEDIR);
            if (root >= 0)
                close(root);
            if (base >= 0)
                close(base);
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
}

namespace hyprmasq {
    using namespace input;

    void initEis(void* handle) {
        socketNonce = std::random_device{}();
        initFocusPolicy(handle);
        monitorLayout = Event::bus()->m_events.monitor.layoutChanged.listen([] {
            for (auto& [_, session] : sessions)
                session->checkRegionsLater();
        });
    }
    void shutdownEis() {
        monitorLayout.reset();
        shutdownFocusPolicy();
        for (auto& [_, session] : sessions)
            session->closing = "unloaded";
        sessions.clear();
        drag::shutdown();
        // libeis has removed every socket and its lock file.
        removeDirectories();
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
        bool madeRoot = false, madeInstance = false;
        int  base     = open(runtime, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        int  root     = base < 0 ? -1 : privateDir(base, "hyprmasq", madeRoot);
        int  instance = root < 0 ? -1 : privateDir(root, directory.c_str(), madeInstance);
        if (madeRoot || madeInstance) {
            made.runtime  = runtime;
            made.instance = directory;
            made.root     = made.root || madeRoot;
            made.own      = made.own || madeInstance;
        }
        if (base >= 0)
            close(base);
        if (root >= 0)
            close(root);
        if (instance < 0)
            return std::unexpected(SError{eErrorCode::INTERNAL, "cannot create private hyprmasq socket directory"});
        close(instance);
        auto  session = std::make_unique<SSession>(w, std::move(path), std::format("{:x}-{}", socketNonce, ++socketCount), force);
        auto* s       = session.get();
        s->context    = eis_new(nullptr);
        if (!s->context || eis_setup_backend_socket(s->context, s->path.c_str()) != 0)
            return std::unexpected(SError{eErrorCode::INTERNAL, "cannot create EIS socket " + s->path});
        // Enforce permissions on the pathname too, independently of libeis or the process umask.
        if (chmod(s->path.c_str(), 0600) != 0)
            return std::unexpected(SError{eErrorCode::INTERNAL, "cannot restrict EIS socket permissions"});
        s->fdSource = wl_event_loop_add_fd(
            g_pCompositor->m_wlEventLoop, eis_get_fd(s->context), WL_EVENT_READABLE, [](int, uint32_t, void* data) { return static_cast<SSession*>(data)->poll(); }, s);
        s->timer = wl_event_loop_add_timer(g_pCompositor->m_wlEventLoop, [](void* data) { return static_cast<SSession*>(data)->tick(); }, s);
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
                            .drags            = session->drags,
                            .lastPause        = std::string{session->lastPause}};
    }
    bool starved(const PHLWINDOW& window) {
        if (rendered(window))
            return false;
        if (auto* session = sessionOf(window); session && session->content->fed())
            return false;
        bool waiting = false;
        forEachSurface(window, [&](SP<CWLSurfaceResource> s) { waiting = waiting || !s->m_current.callbacks.empty(); });
        return waiting;
    }
    bool outdated(const PHLWINDOW& window) {
        if (rendered(window))
            return false;
        auto* session = sessionOf(window);
        if (!session || !session->content->fed() || !session->content->changedSinceFrames())
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
