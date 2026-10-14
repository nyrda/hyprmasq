#pragma once

#include "Error.hpp"
#include "Windows.hpp"

#include <hyprland/src/helpers/math/Math.hpp>
#include <hyprland/src/helpers/signal/Signal.hpp>
#include <hyprland/src/helpers/time/Time.hpp>

#include <cstdint>
#include <deque>
#include <expected>
#include <future>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

struct wl_event_source;
struct SSurfaceState;
class CWLSurfaceResource;

namespace hyprmasq::wait {
    // Counts a window's commits: commits with a new buffer or damage on its surface, its subsurfaces and its popups,
    // and popups or subsurfaces that appear or go away. It follows the creation of popups and subsurfaces as it
    // happens, so it misses none between two reads. Must not be moved.
    //
    // It also tells animation apart from other drawing by the box that each commit damages, in window-local logical
    // pixels: a commit is part of an animation when it damages a small box that commits have damaged again and again
    // for a while, such as a progress bar, a video or a spinner, and it comes well after the last input. lastChange is
    // the time of the last commit that is not.
    class CContentWatch {
      public:
        explicit CContentWatch(const PHLWINDOW& window);
        ~CContentWatch();
        CContentWatch(const CContentWatch&)            = delete;
        CContentWatch& operator=(const CContentWatch&) = delete;

        // Sends the window's surfaces frame callbacks, unless the window is rendered and gets them anyway, at most every
        // 16 ms however many callers ask.
        void sendFrames();
        // True once sendFrames has answered a frame callback that the window waited for.
        bool fed() const {
            return m_fed;
        }

        uint64_t            commits          = 0; // never reset
        Time::steady_tp     lastCommit       = Time::steadyNow();
        Time::steady_tp     lastChange       = lastCommit; // of the last commit that is not part of an animation
        uint64_t            lastChangeCommit = 0;          // the value of commits at that commit
        std::optional<CBox> lastAnimated;                  // the box of the last commit, when it is part of an animation
        Time::steady_tp     lastInput = {};                // when emulated input last reached the window, set by its socket
        CSignalT<>          committed;                     // after commits has risen

      private:
        struct SWatched {
            WP<CWLSurfaceResource>           surface;
            std::vector<CHyprSignalListener> listeners;
        };
        struct SDamage {
            Time::steady_tp at;
            CBox            box;
        };
        void scan();
        void scanLater();
        // Counts commits that damaged the box, or an unknown part of the window without one.
        void                                    count(uint64_t n = 1, std::optional<CBox> box = std::nullopt);
        bool                                    animated(const CBox& box, Time::steady_tp now) const;
        std::optional<CBox>                     damageBox(const SP<CWLSurfaceResource>& surface, const SSurfaceState& state) const;
        SWatched                                watch(const SP<CWLSurfaceResource>& surface);

        PHLWINDOWREF                            m_window;
        std::map<CWLSurfaceResource*, SWatched> m_surfaces;
        std::deque<SDamage>                     m_damage; // the commits of the last few seconds that damaged a box
        wl_event_source*                        m_rescan     = nullptr;
        Time::steady_tp                         m_lastFrames = {};
        bool                                    m_fed        = false;
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
        // The change phase waits for a commit that is not part of an animation after the baseline.
        bool ignoreAnimations = false;
    };

    struct SChangeResult {
        std::string_view      ended; // quiet, no_redraw, timeout, window_closed or cancelled
        uint64_t              commits = 0, commitsSince = 0;
        std::string           socketId;                   // of the socket whose counter the wait read
        int                   quietMs = 0, elapsedMs = 0; // quiet: nothing drawn but animation for this long
        bool                  resizing = false;           // see hyprmasq::resizing, when the wait ended
        std::vector<CBox>     animated;                   // the animations drawn during the wait, which it did not wait for
        std::optional<SError> error;                      // set when the wait ended without an answer
    };

    // The wait against a commit counter baseline: an optional change phase, then a quiet phase, within a deadline. The
    // quiet phase does not wait for animation (see CContentWatch). A window that has not caught up with a resize is not
    // quiet. The window needs an open socket. Main thread only; the future must be read on another thread.
    std::expected<std::future<SChangeResult>, SError> startChange(const PHLWINDOW& window, const SChangeOptions& options);
    // Ends the pending wait with this id. False if there is none, or it ended otherwise at the same moment.
    bool cancel(const std::string& id);

    // Answers every pending wait. Call before deferred::shutdown(), which waits for the threads reading the results.
    void shutdown();
}
