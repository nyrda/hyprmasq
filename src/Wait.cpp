#include "Wait.hpp"
#include "Eis.hpp"

#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/helpers/time/Time.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#include <hyprland/src/protocols/types/SurfaceState.hpp>

#include <chrono>
#include <map>
#include <memory>
#include <set>
#include <vector>

namespace hyprmasq::wait {
    namespace {
        using std::chrono::milliseconds;

        struct SWaiter {
            PHLWINDOWREF          window;
            milliseconds          quiet, timeout;
            Time::steady_tp       start = Time::steadyNow();
            CContentWatch         content;
            wl_event_source*      timer = nullptr;
            std::promise<SResult> promise;

            ~SWaiter() {
                if (timer)
                    wl_event_source_remove(timer);
            }

            SResult result(bool settled, std::string error = "") const {
                std::optional<int> sinceInput;
                if (const auto w = window.lock())
                    if (const auto input = inputState(w))
                        sinceInput = input->frames;
                return {.settled          = settled,
                        .waitedMs         = sc<int>(std::chrono::duration_cast<milliseconds>(Time::steadyNow() - start).count()),
                        .frames           = content.frames,
                        .framesSinceInput = sinceInput,
                        .error            = std::move(error)};
            }
        };

        std::vector<std::unique_ptr<SWaiter>> waiters;

        // Removing the timer from within its own callback is fine: libwayland frees it after the dispatch.
        void finish(SWaiter* waiter, SResult result) {
            waiter->promise.set_value(std::move(result));
            std::erase_if(waiters, [waiter](const auto& w) { return w.get() == waiter; });
        }

        int tick(void* data) {
            auto*      waiter = static_cast<SWaiter*>(data);
            const auto w      = waiter->window.lock();
            if (!w || !w->m_isMapped) {
                finish(waiter, waiter->result(false, "the window closed"));
                return 0;
            }
            waiter->content.scan(w);
            const auto now   = Time::steadyNow();
            auto       since = std::max(waiter->content.lastChange, waiter->start);
            if (const auto input = inputState(w); input && input->lastInput > since)
                since = input->lastInput;
            if (now - since >= waiter->quiet)
                finish(waiter, waiter->result(true));
            else if (now - waiter->start >= waiter->timeout)
                finish(waiter, waiter->result(false));
            else
                wl_event_source_timer_update(waiter->timer, 10);
            return 0;
        }
    }

    void CContentWatch::scan(const PHLWINDOW& window) {
        const auto                    now = Time::steadyNow();
        std::set<CWLSurfaceResource*> present;
        forEachSurface(window, [&](SP<CWLSurfaceResource> s) {
            present.insert(s.get());
            if (auto it = m_surfaces.find(s.get()); it != m_surfaces.end() && !it->second.surface.expired())
                return;
            if (m_scanned) {
                lastChange = now;
                ++frames;
            }
            m_surfaces[s.get()] = {.surface = s, .commit = s->m_events.stateCommit.listen([this](WP<SSurfaceState> state) {
                                       // The state queue owns its states through unique pointers, which cannot be locked.
                                       const auto* st = state.get();
                                       if (st && (st->updated.bits.buffer || st->updated.bits.damage)) {
                                           lastChange = Time::steadyNow();
                                           ++frames;
                                       }
                                   })};
        });
        if (std::erase_if(m_surfaces, [&](const auto& entry) { return !present.contains(entry.first) || entry.second.surface.expired(); }) > 0 && m_scanned) {
            lastChange = now;
            ++frames;
        }
        m_scanned = true;
    }

    std::expected<std::future<SResult>, std::string> start(const PHLWINDOW& window, int quietMs, int timeoutMs) {
        auto waiter     = std::make_unique<SWaiter>();
        waiter->window  = window;
        waiter->quiet   = milliseconds(quietMs);
        waiter->timeout = milliseconds(timeoutMs);
        waiter->timer   = wl_event_loop_add_timer(g_pCompositor->m_wlEventLoop, tick, waiter.get());
        if (!waiter->timer)
            return std::unexpected("cannot start a timer");
        waiter->content.scan(window);
        wl_event_source_timer_update(waiter->timer, 10);
        auto future = waiter->promise.get_future();
        waiters.push_back(std::move(waiter));
        return future;
    }

    void shutdown() {
        for (auto& waiter : std::exchange(waiters, {}))
            waiter->promise.set_value(waiter->result(false, "hyprmasq was unloaded"));
    }
}
