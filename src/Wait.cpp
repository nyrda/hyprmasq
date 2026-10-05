#include "Wait.hpp"
#include "Eis.hpp"

#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/helpers/time/Time.hpp>
#include <hyprland/src/protocols/XDGShell.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#include <hyprland/src/protocols/core/Subcompositor.hpp>
#include <hyprland/src/protocols/types/SurfaceState.hpp>
#include <hyprland/src/render/Renderer.hpp>

#include <algorithm>
#include <chrono>
#include <format>
#include <map>
#include <memory>
#include <set>
#include <vector>

namespace hyprmasq::wait {
    namespace {
        using std::chrono::milliseconds;

        int elapsedMs(Time::steady_tp since, Time::steady_tp now) {
            return sc<int>(std::chrono::duration_cast<milliseconds>(now - since).count());
        }

        struct SWaiter {
            PHLWINDOWREF          window;
            milliseconds          quiet, timeout;
            Time::steady_tp       start = Time::steadyNow();
            CContentWatch         content;
            wl_event_source*      timer = nullptr;
            std::promise<SResult> promise;

            SWaiter(const PHLWINDOW& w, int quietMs, int timeoutMs) : window(w), quiet(quietMs), timeout(timeoutMs), content(w) {}
            ~SWaiter() {
                if (timer)
                    wl_event_source_remove(timer);
            }

            SResult result(bool settled, std::optional<SError> error = std::nullopt) const {
                std::optional<int> sinceInput;
                if (const auto w = window.lock())
                    if (const auto socket = socketState(w))
                        sinceInput = socket->framesSinceInput;
                return {.settled          = settled,
                        .waitedMs         = elapsedMs(start, Time::steadyNow()),
                        .frames           = sc<int>(content.commits),
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
                finish(waiter, waiter->result(false, SError{eErrorCode::WINDOW_GONE, "the window closed"}));
                return 0;
            }
            const auto now   = Time::steadyNow();
            auto       since = std::max(waiter->content.lastCommit, waiter->start);
            if (const auto socket = socketState(w); socket && socket->lastInput > since)
                since = socket->lastInput;
            if (now - since >= waiter->quiet)
                finish(waiter, waiter->result(true));
            else if (now - waiter->start >= waiter->timeout)
                finish(waiter, waiter->result(false));
            else
                wl_event_source_timer_update(waiter->timer, 10);
            return 0;
        }

        struct SChangeWaiter {
            PHLWINDOWREF                   window;
            std::shared_ptr<CContentWatch> content;
            std::string                    socketId; // of the socket that content counts for
            SChangeOptions                 options;
            Time::steady_tp                start = Time::steadyNow(), lastInput = start;
            bool                           changing = false; // in the change phase
            wl_event_source*               timer    = nullptr;
            CHyprSignalListener            commit, unmap, destroy;
            std::promise<SChangeResult>    promise;

            ~SChangeWaiter() {
                if (timer)
                    wl_event_source_remove(timer);
            }

            // Signal handlers only wake the timer, which may end the wait, so nothing is destroyed while a signal runs.
            void wake() {
                wl_event_source_timer_update(timer, 1);
            }

            SChangeResult result(std::string_view ended) const {
                const auto now = Time::steadyNow();
                const auto w   = window.lock();
                return {.ended        = ended,
                        .commits      = content->commits,
                        .commitsSince = content->commits > options.since ? content->commits - options.since : 0,
                        .socketId     = socketId,
                        .quietMs      = elapsedMs(std::max({content->lastCommit, lastInput, start}), now),
                        .elapsedMs    = elapsedMs(start, now),
                        .resizing     = w && w->m_isMapped && hyprmasq::resizing(w),
                        .error        = std::nullopt};
            }
        };

        std::vector<std::unique_ptr<SChangeWaiter>> changeWaiters;

        void                                        finish(SChangeWaiter* waiter, SChangeResult result) {
            waiter->promise.set_value(std::move(result));
            std::erase_if(changeWaiters, [waiter](const auto& w) { return w.get() == waiter; });
        }

        bool closed(const SChangeWaiter* waiter) {
            const auto w = waiter->window.lock();
            return !w || !w->m_isMapped;
        }

        // Ends the wait once an ending holds, otherwise looks again for the next frame.
        void check(SChangeWaiter* waiter) {
            const auto now = Time::steadyNow();
            const auto w   = waiter->window.lock();
            if (!w || !w->m_isMapped) {
                finish(waiter, waiter->result("window_closed"));
                return;
            }
            // Once the socket is gone, no more input arrives.
            if (const auto socket = socketState(w))
                waiter->lastInput = socket->lastInput;
            waiter->content->sendFrames();

            const auto&      options    = waiter->options;
            const auto       deadlineAt = waiter->start + milliseconds(options.deadlineMs);
            Time::steady_tp  phaseEnd;
            std::string_view phaseEnding;
            // A window that has not caught up with a resize shows a frame of another size, so it is not quiet yet.
            bool behind = false;
            if (waiter->changing) {
                phaseEnd    = waiter->start + milliseconds(*options.expectMs);
                phaseEnding = "no_redraw";
            } else {
                phaseEnd    = std::max({waiter->content->lastCommit, waiter->lastInput, waiter->start}) + milliseconds(options.quietMs);
                phaseEnding = "quiet";
                behind      = hyprmasq::resizing(w);
            }
            // The phase's ending wins a tie with the deadline: it holds just as well and says more.
            if (!behind && phaseEnd <= now && phaseEnd <= deadlineAt) {
                finish(waiter, waiter->result(phaseEnding));
                return;
            }
            if (deadlineAt <= now) {
                finish(waiter, waiter->result("timeout"));
                return;
            }
            // The window can stop being rendered at any moment, and then only this sends it frame callbacks.
            const auto next  = behind ? deadlineAt : std::min(phaseEnd, deadlineAt);
            const auto delay = std::clamp<int64_t>(std::chrono::ceil<milliseconds>(next - now).count(), 1, 16);
            wl_event_source_timer_update(waiter->timer, sc<int>(delay));
        }
    }

    CContentWatch::CContentWatch(const PHLWINDOW& window) : m_window(window) {
        forEachSurface(window, [&](SP<CWLSurfaceResource> s) { m_surfaces[s.get()] = watch(s); });
    }

    CContentWatch::~CContentWatch() {
        if (m_rescan)
            wl_event_source_remove(m_rescan);
    }

    void CContentWatch::count(uint64_t n) {
        commits += n;
        lastCommit = Time::steadyNow();
        committed.emit();
    }

    CContentWatch::SWatched CContentWatch::watch(const SP<CWLSurfaceResource>& s) {
        SWatched watched{.surface = s, .listeners = {}};
        auto&    listeners = watched.listeners;
        listeners.push_back(s->m_events.stateCommit.listen([this](WP<SSurfaceState> state) {
            // The state queue owns its states through unique pointers, which cannot be locked.
            const auto* st = state.get();
            if (st && (st->updated.bits.buffer || st->updated.bits.damage))
                count();
        }));
        // A null buffer, which unmaps the surface, is applied at once without a queued state.
        listeners.push_back(s->m_events.precommit.listen([this, surface = WP<CWLSurfaceResource>(s)] {
            if (surface && !surface->m_pending.buffer && surface->m_pending.updated.bits.buffer)
                count();
        }));
        // The new subsurface is already in the tree, and a buffer for it can follow in the same batch of requests.
        listeners.push_back(s->m_events.newSubsurface.listen([this] { scan(); }));
        listeners.push_back(s->m_events.destroy.listen([this] { scanLater(); }));
        if (s->m_role->role() == SURFACE_ROLE_SUBSURFACE) {
            if (auto subsurface = sc<CSubsurfaceRole*>(s->m_role.get())->m_subsurface.lock())
                listeners.push_back(subsurface->m_events.destroy.listen([this] { scanLater(); }));
        } else if (s->m_role->role() == SURFACE_ROLE_XDG_SHELL) {
            if (auto xdg = sc<CXDGSurfaceRole*>(s->m_role.get())->m_xdgSurface.lock()) {
                // Hyprland adds a new popup to the window's popup tree in a listener of its own, which may run after
                // this one. A popup can draw only after a configure round trip, so a scan once the batch is handled
                // sees it before its first buffer.
                listeners.push_back(xdg->m_events.newPopup.listen([this] { scanLater(); }));
                listeners.push_back(xdg->m_events.destroy.listen([this] { scanLater(); }));
                if (auto popup = xdg->m_popup.lock())
                    listeners.push_back(popup->m_events.destroy.listen([this] { scanLater(); }));
            }
        }
        return watched;
    }

    void CContentWatch::scan() {
        const auto window = m_window.lock();
        if (!window)
            return;
        std::set<CWLSurfaceResource*> present;
        uint64_t                      changes = 0;
        forEachSurface(window, [&](SP<CWLSurfaceResource> s) {
            present.insert(s.get());
            if (auto it = m_surfaces.find(s.get()); it != m_surfaces.end() && !it->second.surface.expired())
                return;
            m_surfaces[s.get()] = watch(s);
            ++changes;
        });
        if (std::erase_if(m_surfaces, [&](const auto& entry) { return !present.contains(entry.first) || entry.second.surface.expired(); }) > 0)
            ++changes;
        if (changes > 0)
            count(changes);
    }

    // Destruction signals come before the surface leaves the tree, and a new popup joins it in a listener that may run
    // after this one. Idle sources run once the current batch of events is handled.
    void CContentWatch::scanLater() {
        if (m_rescan)
            return;
        m_rescan = wl_event_loop_add_idle(
            g_pCompositor->m_wlEventLoop,
            [](void* data) {
                auto* self     = static_cast<CContentWatch*>(data);
                self->m_rescan = nullptr; // libwayland removes it after this call
                self->scan();
            },
            this);
    }

    void CContentWatch::sendFrames() {
        const auto window = m_window.lock();
        const auto now    = Time::steadyNow();
        if (!window || !window->m_isMapped || g_pHyprRenderer->shouldRenderWindow(window) || now - m_lastFrames < milliseconds(16))
            return;
        m_lastFrames = now;
        forEachSurface(window, [&](SP<CWLSurfaceResource> s) { s->frame(now); });
    }

    std::expected<std::future<SResult>, SError> start(const PHLWINDOW& window, int quietMs, int timeoutMs) {
        auto waiter   = std::make_unique<SWaiter>(window, quietMs, timeoutMs);
        waiter->timer = wl_event_loop_add_timer(g_pCompositor->m_wlEventLoop, tick, waiter.get());
        if (!waiter->timer)
            return std::unexpected(SError{eErrorCode::INTERNAL, "cannot start a timer"});
        wl_event_source_timer_update(waiter->timer, 10);
        auto future = waiter->promise.get_future();
        waiters.push_back(std::move(waiter));
        return future;
    }

    std::expected<std::future<SChangeResult>, SError> startChange(const PHLWINDOW& window, const SChangeOptions& options) {
        const auto socket = socketState(window);
        if (!socket)
            return std::unexpected(SError{eErrorCode::NO_SOCKET, std::format("no open socket for {}", windowAddress(window))});
        // The counter never falls, so a higher baseline came from another counter, such as one of an earlier socket.
        if (options.since > socket->content->commits)
            return std::unexpected(SError{eErrorCode::INVALID_ARGUMENTS, std::format("since {} is above the commit counter, {}", options.since, socket->content->commits)});
        if (!options.id.empty() && std::ranges::any_of(changeWaiters, [&](const auto& w) { return w->options.id == options.id; }))
            return std::unexpected(SError{eErrorCode::INVALID_ARGUMENTS, std::format("a wait with id {} is pending", options.id)});

        auto waiter       = std::make_unique<SChangeWaiter>();
        auto raw          = waiter.get();
        waiter->window    = window;
        waiter->content   = socket->content;
        waiter->socketId  = socket->id;
        waiter->options   = options;
        waiter->lastInput = socket->lastInput;
        waiter->changing  = options.expectMs && socket->content->commits <= options.since;
        waiter->timer     = wl_event_loop_add_timer(
            g_pCompositor->m_wlEventLoop,
            [](void* data) {
                check(static_cast<SChangeWaiter*>(data));
                return 0;
            },
            raw);
        if (!waiter->timer)
            return std::unexpected(SError{eErrorCode::INTERNAL, "cannot start a timer"});
        // The change phase ends at the first commit after the baseline, unless expect ran out before it.
        waiter->commit  = socket->content->committed.listen([raw] {
            if (raw->changing && raw->content->commits > raw->options.since && Time::steadyNow() < raw->start + milliseconds(*raw->options.expectMs)) {
                raw->changing = false;
                raw->wake();
            }
        });
        waiter->unmap   = window->m_events.unmap.listen([raw] { raw->wake(); });
        waiter->destroy = window->m_events.destroy.listen([raw] { raw->wake(); });
        auto future     = waiter->promise.get_future();
        changeWaiters.push_back(std::move(waiter));
        check(raw);
        return future;
    }

    bool cancel(const std::string& id) {
        const auto it = std::ranges::find_if(changeWaiters, [&](const auto& w) { return !id.empty() && w->options.id == id; });
        if (it == changeWaiters.end())
            return false;
        auto* waiter = it->get();
        if (closed(waiter)) {
            finish(waiter, waiter->result("window_closed"));
            return false;
        }
        finish(waiter, waiter->result("cancelled"));
        return true;
    }

    void shutdown() {
        const SError unloaded{eErrorCode::UNLOADED, "hyprmasq was unloaded"};
        for (auto& waiter : std::exchange(waiters, {}))
            waiter->promise.set_value(waiter->result(false, unloaded));
        for (auto& waiter : std::exchange(changeWaiters, {})) {
            SChangeResult result;
            result.error = unloaded;
            waiter->promise.set_value(std::move(result));
        }
    }
}
