#pragma once

#include "Windows.hpp"

#include <hyprland/src/helpers/signal/Signal.hpp>
#include <hyprland/src/helpers/time/Time.hpp>

#include <expected>
#include <future>
#include <map>
#include <string>

namespace hyprmasq::wait {
    // Counts the frames a window draws: commits with a new buffer or damage on its surfaces, its subsurfaces and its
    // popups. A popup or subsurface that appears or goes away counts as a frame too, once a scan sees it. Must not be
    // moved once scanned.
    class CContentWatch {
      public:
        CContentWatch()                                = default;
        CContentWatch(const CContentWatch&)            = delete;
        CContentWatch& operator=(const CContentWatch&) = delete;

        // Watches the window's current surfaces. Call again to pick up surfaces that appeared since.
        void            scan(const PHLWINDOW& window);

        int             frames     = 0;
        Time::steady_tp lastChange = Time::steadyNow();

      private:
        struct SWatched {
            WP<CWLSurfaceResource> surface;
            CHyprSignalListener    commit;
        };
        std::map<CWLSurfaceResource*, SWatched> m_surfaces;
        bool                                    m_scanned = false;
    };

    struct SResult {
        bool               settled  = false;
        int                waitedMs = 0;
        int                frames   = 0;     // drawn during the wait
        std::optional<int> framesSinceInput; // drawn since the last input through the window's socket, if it has one
        std::string        error;            // set when the wait ended without an answer
    };

    // Starts watching the window. The result is ready once nothing new has been drawn and no emulated input has arrived
    // for quietMs, or once timeoutMs have passed. Main thread only; the future must be read on another thread.
    std::expected<std::future<SResult>, std::string> start(const PHLWINDOW& window, int quietMs, int timeoutMs);
    // Answers every pending wait. Call before deferred::shutdown(), which waits for the threads reading the results.
    void shutdown();
}
