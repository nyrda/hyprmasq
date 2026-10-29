#include "Deferred.hpp"
#include "Hooks.hpp"

#include <hyprland/src/Compositor.hpp>

#include <dlfcn.h>
#include <pthread.h>
#include <sys/eventfd.h>
#include <unistd.h>
#include <atomic>
#include <format>
#include <map>
#include <memory>
#include <stdexcept>
#include <thread>
#include <vector>

namespace hyprmasq::deferred {
    namespace {
        using Refuse   = std::move_only_function<std::string(const SError&)>;
        using Resolver = SP<CPromiseResolver<std::string>>;

        struct SJob {
            std::move_only_function<std::string()> work;
            // Set for work that waits for the main thread, see runWaiting().
            Refuse            refuse;
            std::thread       thread;
            std::string       reply;
            std::atomic<bool> done = false;
            // Answers the hyprctl connection, or the batch, waiting for the reply.
            Resolver resolver;
        };

        bool                                      active  = false;
        int                                       eventFd = -1, stopEvent = -1;
        wl_event_source*                          source = nullptr;
        uint64_t                                  nextId = 1;
        std::map<uint64_t, std::unique_ptr<SJob>> jobs;
        int                                       pluginCalls    = 0;
        void*                                     originalInvoke = nullptr;
        std::thread::id                           mainThread;
        // Frames of this library that Hyprland returns into, and the reference that keeps it mapped for them after
        // an unload.
        int   frames = 0;
        void* pinned = nullptr;

        // Runs once Hyprland has returned from the last frame of this library that outlived an unload. dlclose can unmap
        // this library, so libwayland calls it directly instead of code in here.
        void releasePinned() {
            wl_event_loop_add_idle(g_pCompositor->m_wlEventLoop, reinterpret_cast<wl_event_loop_idle_func_t>(reinterpret_cast<void (*)()>(&dlclose)),
                                   std::exchange(pinned, nullptr));
        }

        struct SFrame {
            SFrame() {
                ++frames;
            }
            ~SFrame() {
                if (--frames == 0 && pinned)
                    releasePinned();
            }
        };

        void complete(SJob* job, std::string reply) {
            job->reply = std::move(reply);
            job->done.store(true, std::memory_order_release);
            eventfd_write(eventFd, 1);
        }

        void start(std::move_only_function<std::string()> work, Refuse refuse, Resolver resolver) {
            const auto id  = nextId++;
            auto*      job = (jobs[id] = std::make_unique<SJob>()).get();
            job->work      = std::move(work);
            job->refuse    = std::move(refuse);
            job->resolver  = std::move(resolver);
            auto execute   = [job] {
                std::string reply;
                try {
                    reply = job->work();
                } catch (const std::exception& e) { reply = std::format("error: {}", e.what()); }
                complete(job, std::move(reply));
            };
            try {
                job->thread = std::thread([execute] {
                    pthread_setname_np(pthread_self(), "hyprmasq-job");
                    execute();
                });
            } catch (const std::system_error&) {
                // No thread to spare. Work that waits for the main thread would wait for itself here.
                if (job->refuse)
                    complete(job, job->refuse(SError{eErrorCode::INTERNAL, "no thread to wait in"}));
                else
                    execute();
            }
        }

        // hyprctl sends a promise's result instead of the returned reply, but only for requests from its socket, where
        // a batch waits for all of its promises. The only other way in is HyprlandAPI::invokeHyprctlCommand, whose
        // caller needs the reply at once.
        bool deferrable() {
            return active && pluginCalls == 0;
        }

        // Starts work and returns the promise of its reply.
        IPC::Socket1::SResponse schedule(std::move_only_function<std::string()> work, Refuse refuse) {
            Resolver resolver;
            auto     promise = CPromise<std::string>::make([&](Resolver r) { resolver = r; });
            start(std::move(work), std::move(refuse), std::move(resolver));
            return promise;
        }

        int onEvent(int fd, uint32_t, void*) {
            eventfd_t count = 0;
            eventfd_read(fd, &count);
            std::vector<std::unique_ptr<SJob>> ready;
            for (auto it = jobs.begin(); it != jobs.end();) {
                if (it->second->done.load(std::memory_order_acquire)) {
                    if (it->second->thread.joinable())
                        it->second->thread.join(); // returns at once: the job is done
                    ready.push_back(std::move(it->second));
                    it = jobs.erase(it);
                } else
                    ++it;
            }
            for (auto& job : ready)
                job->resolver->resolve(std::move(job->reply));
            return 0;
        }

        std::string invokeHyprctlCommand(const std::string& call, const std::string& args, const std::string& format) {
            SFrame frame;
            ++pluginCalls;
            using Fn = std::string (*)(const std::string&, const std::string&, const std::string&);
            try {
                auto reply = reinterpret_cast<Fn>(originalInvoke)(call, args, format);
                --pluginCalls;
                return reply;
            } catch (...) {
                --pluginCalls;
                throw;
            }
        }
    }

    void init(void* handle) {
        // HyprlandAPI::invokeHyprctlCommand has C linkage.
        originalInvoke = hook(handle, "invokeHyprctlCommand", reinterpret_cast<void*>(&invokeHyprctlCommand));
        eventFd        = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
        source         = eventFd < 0 ? nullptr : wl_event_loop_add_fd(g_pCompositor->m_wlEventLoop, eventFd, WL_EVENT_READABLE, onEvent, nullptr);
        // Wakes the jobs at the unload, as eventFd wakes the main thread when one is done.
        stopEvent = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
        if (!source || stopEvent < 0)
            throw std::runtime_error("hyprmasq: cannot watch background work");
        mainThread = std::this_thread::get_id();
        active     = true;
    }

    void shutdown() {
        if (frames > 0) {
            // Unloaded from within a plugin's hyprctl call, which returns into this library afterwards. Keep it mapped
            // until it has.
            Dl_info info{};
            if (!pinned && dladdr(reinterpret_cast<void*>(&shutdown), &info) && info.dli_fname)
                pinned = dlopen(info.dli_fname, RTLD_NOW | RTLD_NOLOAD);
        }
        active = false;

        // wait::shutdown() has answered every wait, and the stop event ends every write that waits for a pipe, so every
        // job ends soon.
        if (stopEvent >= 0)
            eventfd_write(stopEvent, 1);
        for (auto& [_, job] : jobs) {
            if (job->thread.joinable())
                job->thread.join();
        }
        if (stopEvent >= 0)
            close(stopEvent);
        stopEvent = -1;
        for (auto& [_, job] : std::exchange(jobs, {}))
            job->resolver->resolve(std::move(job->reply));

        if (source)
            wl_event_source_remove(source);
        source = nullptr;
        if (eventFd >= 0)
            close(eventFd);
        eventFd = -1;
    }

    int stopFd() {
        return stopEvent;
    }

    bool canDefer() {
        return active && deferrable();
    }

    // A job gets its own thread only when its reply is deferred, and nothing waits for it then: a request that cannot be
    // deferred runs its work at once on the calling thread, the main thread.
    bool waited() {
        return std::this_thread::get_id() == mainThread;
    }

    IPC::Socket1::SResponse run(std::move_only_function<std::string()> work) {
        if (!deferrable())
            return work();
        return schedule(std::move(work), nullptr);
    }

    IPC::Socket1::SResponse runWaiting(std::move_only_function<std::string()> work, std::move_only_function<std::string(const SError&)> refuse) {
        if (!deferrable())
            return refuse(SError{eErrorCode::UNSUPPORTED, "wait only works in requests to hyprctl's socket"});
        return schedule(std::move(work), std::move(refuse));
    }
}
