#pragma once
#include "Error.hpp"
#include "Windows.hpp"
#include <hyprland/src/helpers/signal/Signal.hpp>
#include <hyprland/src/helpers/time/Time.hpp>
#include <cstdint>
#include <deque>
#include <expected>
#include <memory>
#include <optional>
#include <string>
#include <vector>

struct wl_client;

namespace hyprmasq {
    namespace wait {
        class CContentWatch;
    }

    struct SEisInfo {
        std::string address, path;
        std::string id; // differs for every socket, also one opened again for the same window
        bool        force = false;
    };

    void initEis(void* handle);
    void shutdownEis();
    // force: drive X11 windows even while another window of their X server has the focus.
    std::expected<SEisInfo, SError> openEis(const PHLWINDOW& window, bool force);
    bool closeEis(const std::string& address);
    std::vector<SEisInfo> listEis();

    struct SLeaseState {
        int         remainingMs = 0;
        std::string owner;
    };

    // The drag-and-drop operations that a driven window's app started while its socket was open, see "Drag-and-drop" in
    // docs/automation.md. A wait holds it to see them end.
    struct SDragRecord {
        uint64_t    id        = 0;      // counts them, so the last one has this id; 0 before the first
        std::string phase     = "none"; // of the last one: none, active, dropped, finished, cancelled or unconfirmed
        bool        privately = false;  // the last one ran as a private drag, see Drag.hpp
        std::string reason;             // why the last one was cancelled or unconfirmed
        // Of them, the private drags that started, that the destination finished, and that were cancelled, also
        // before they started, or that the destination did not finish in time after the drop.
        uint64_t        started = 0, finished = 0, cancelled = 0, unconfirmed = 0;
        Time::steady_tp endedAt    = {}; // when the last one ended
        uint64_t        unfinished = 0;  // private drags that have not ended, also ones before the last
        // How the last ones ended, the latest last, so that a caller sees each one that ended since it last looked:
        // up to MAX_ENDED of them.
        struct SEnded {
            uint64_t    id = 0;
            std::string phase, reason;
        };
        static constexpr size_t MAX_ENDED = 32;
        std::deque<SEnded>      ended;
        CSignalT<>              changed; // after any of these changed
        // Records that the drag with the id ended so.
        void end(uint64_t drag, std::string_view how, std::string_view why) {
            ended.push_back({.id = drag, .phase = std::string{how}, .reason = std::string{why}});
            if (ended.size() > MAX_ENDED)
                ended.pop_front();
        }
        // A private drag has not ended: the last one, or one before it, as when the last was refused as busy.
        bool pending() const {
            return unfinished > 0;
        }
    };

    struct SSocketState {
        std::string                          path;
        std::string                          id; // of this socket, see SEisInfo
        bool                                 force = false;
        Time::steady_tp                      lastInput;            // when emulated input last reached the window
        int                                  framesSinceInput = 0; // commits since then
        std::shared_ptr<wait::CContentWatch> content;              // the window's commit counter, kept while the socket is open
        std::optional<SLeaseState>           lease;                // while one is held
        // Times the user's own input to the window's Wayland connection took the keyboard focus back from the window
        // before a shortcut or function key had settled, which may have kept it from taking effect.
        uint64_t                     interrupted = 0;
        std::shared_ptr<SDragRecord> drags; // kept while the socket is open
        // Why the plugin last paused the socket's devices, which stops the sender with input_paused, or empty if it never
        // did: x11_focus, x11_unresponsive, user_button or user_key, see "Window state" in docs/automation.md.
        std::string lastPause;
    };
    // For a window with a socket. Main thread only.
    std::optional<SSocketState> socketState(const PHLWINDOW& window);

    // True while the window is not rendered, has asked for a frame callback that it has not got, and has got none from
    // hyprmasq: its socket has sent it none since it opened, or it has no socket. It may not have drawn what it would
    // draw on screen, such as an application that started on a hidden workspace. Main thread only.
    bool starved(const PHLWINDOW& window);

    // Starts or renews the focus lease of a window with a socket for ms milliseconds, see "Focus lease" in docs/automation.md.
    std::expected<SLeaseState, SError> holdLease(const PHLWINDOW& window, int ms, const std::string& owner);
    // Ends the window's lease, only if owner holds it when one is given. False if no lease ended.
    bool releaseLease(const PHLWINDOW& window, const std::optional<std::string>& owner);
    // True while a lease keeps the emulated pointer on the window.
    bool leaseKeepsPointer(const PHLWINDOW& window);

    // What input can do in a window at the moment, see "Window state" in docs/automation.md.
    struct SCapabilities {
        bool lease = false, hold = false, dnd = false;
        bool privateDnd = false; // a drag-and-drop that emulated input starts runs as a private drag, see Drag.hpp
        bool x11Focus   = false; // keys follow X input focus, which the plugin moves
    };
    SCapabilities capabilities(const PHLWINDOW& window);

    // True while a socket is open for a window of the Wayland client. Main thread only.
    bool drivesClient(wl_client* client);
    // True while emulated input has the keyboard, or the pointer, of a surface of the Wayland client. Main thread only.
    bool emulatedKeyboardOn(wl_client* client);
    bool emulatedPointerOn(wl_client* client);
}
