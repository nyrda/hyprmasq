#pragma once

#include "Error.hpp"
#include "Windows.hpp"

#include <hyprland/src/helpers/signal/Signal.hpp>
#include <hyprland/src/helpers/time/Time.hpp>

#include <cstdint>
#include <expected>
#include <future>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

struct wl_event_source;

namespace hyprmasq::wait {
    // Counts a window's commits: commits with a new buffer or damage on its surface, its subsurfaces and its popups,
    // and popups or subsurfaces that appear or go away. It follows the creation of popups and subsurfaces as it
    // happens, so it misses none between two reads. Must not be moved.
    class CContentWatch {
      public:
        explicit CContentWatch(const PHLWINDOW& window);
        ~CContentWatch();
        CContentWatch(const CContentWatch&)            = delete;
        CContentWatch& operator=(const CContentWatch&) = delete;

        // Sends the window's surfaces frame callbacks, unless the window is rendered and gets them anyway, at most every
        // 16 ms however many callers ask.
        void            sendFrames();

        uint64_t        commits    = 0; // never reset
        Time::steady_tp lastCommit = Time::steadyNow();
        CSignalT<>      committed; // after commits has risen

      private:
        struct SWatched {
            WP<CWLSurfaceResource>           surface;
            std::vector<CHyprSignalListener> listeners;
        };
        void                                    scan();
        void                                    scanLater();
        void                                    count(uint64_t n = 1);
        SWatched                                watch(const SP<CWLSurfaceResource>& surface);

        PHLWINDOWREF                            m_window;
        std::map<CWLSurfaceResource*, SWatched> m_surfaces;
        wl_event_source*                        m_rescan     = nullptr;
        Time::steady_tp                         m_lastFrames = {};
    };

    struct SResult {
        bool                  settled  = false;
        int                   waitedMs = 0;
        int                   frames   = 0;     // drawn during the wait
        std::optional<int>    framesSinceInput; // drawn since the last input through the window's socket, if it has one
        std::optional<SError> error;            // set when the wait ended without an answer
    };

    // Starts watching the window. The result is ready once nothing new has been drawn and no emulated input has arrived
    // for quietMs, or once timeoutMs have passed. Main thread only; the future must be read on another thread.
    std::expected<std::future<SResult>, SError> start(const PHLWINDOW& window, int quietMs, int timeoutMs);

    struct SChangeOptions {
        uint64_t           since = 0;
        std::optional<int> expectMs;
        int                quietMs = 200, deadlineMs = 5000;
        std::string        id;
    };

    struct SChangeResult {
        std::string_view      ended; // quiet, no_redraw, timeout, window_closed or cancelled
        uint64_t              commits = 0, commitsSince = 0;
        std::string           socketId; // of the socket whose counter the wait read
        int                   quietMs = 0, elapsedMs = 0;
        bool                  resizing = false; // see hyprmasq::resizing, when the wait ended
        std::optional<SError> error;            // set when the wait ended without an answer
    };

    // The wait against a commit counter baseline: an optional change phase, then a quiet phase, within a deadline. A
    // window that has not caught up with a resize is not quiet. The window needs an open socket. Main thread only; the
    // future must be read on another thread.
    std::expected<std::future<SChangeResult>, SError> startChange(const PHLWINDOW& window, const SChangeOptions& options);
    // Ends the pending wait with this id. False if there is none, or it ended otherwise at the same moment.
    bool cancel(const std::string& id);

    // Answers every pending wait. Call before deferred::shutdown(), which waits for the threads reading the results.
    void shutdown();
}
