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
    // It also tells animation apart from other drawing by the boxes that each commit damages, in window-local logical
    // pixels, one around each part of its damage: a commit is part of an animation when each of them is a small box that
    // commits have damaged again and again for a while, such as a progress bar, a video or a spinner, and it comes well
    // after the last input, or a thin box that commits have damaged again and again since the last input, such as an
    // overlay scrollbar that fades in or a caret that blinks. lastChange is the time of the last commit that is not.
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
        // True when the window has drawn a change, a commit that is not part of an animation, since sendFrames last ran.
        bool changedSinceFrames() const {
            return lastChange > m_lastFrames;
        }

        uint64_t          commits          = 0; // never reset
        Time::steady_tp   lastCommit       = Time::steadyNow();
        Time::steady_tp   lastChange       = lastCommit; // of the last commit that is not part of an animation
        uint64_t          lastChangeCommit = 0;          // the value of commits at that commit
        std::vector<CBox> lastAnimated;                  // the boxes of the last commit, when it is part of an animation
        Time::steady_tp   lastInput = {};                // when emulated input last reached the window, set by its socket
        CSignalT<>        committed;                     // after commits has risen

      private:
        struct SWatched {
            WP<CWLSurfaceResource>           surface;
            std::vector<CHyprSignalListener> listeners;
        };
        struct SDamage {
            Time::steady_tp at;
            CBox            box;
            uint64_t        commit; // the value of commits at the commit that damaged it
        };
        void scan();
        void scanLater();
        // Counts commits that damaged the boxes, or an unknown part of the window without any.
        void count(uint64_t n = 1, std::vector<CBox> boxes = {});
        bool animated(const CBox& box, Time::steady_tp now) const;
        std::vector<CBox> damageBoxes(const SP<CWLSurfaceResource>& surface, const SSurfaceState& state) const;
        SWatched watch(const SP<CWLSurfaceResource>& surface);

        PHLWINDOWREF                            m_window;
        std::map<CWLSurfaceResource*, SWatched> m_surfaces;
        std::deque<SDamage>                     m_damage; // the boxes that commits damaged in the last few seconds
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
        // hyprctl stops reading a reply after 5 seconds, so the default deadline answers before.
        int         quietMs = 200, deadlineMs = 4500;
        std::string id;
        // The change phase waits for a commit that is not part of an animation after the baseline.
        bool ignoreAnimations = false;
        // The id of the last drag-and-drop of the socket that the caller knows of. While a later one goes on, before
        // its drop or the destination's finish, neither phase ends. Its end ends the change phase, as a redraw does,
        // and the quiet phase counts from it.
        std::optional<uint64_t> drags;
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
