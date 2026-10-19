#include "Deferred.hpp"
#include "Hooks.hpp"

#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/debug/HyprCtl.hpp>

#include <dlfcn.h>
#include <pthread.h>
#include <sys/eventfd.h>
#include <unistd.h>
#include <algorithm>
#include <atomic>
#include <format>
#include <map>
#include <memory>
#include <random>
#include <stdexcept>
#include <thread>
#include <vector>

namespace hyprmasq::deferred {
    namespace {
        using Commands = std::vector<SP<SHyprCtlCommand>>;
        Commands& commandsOf(CHyprCtl& ctl);
        // CHyprCtl::m_commands is private. An explicit instantiation may name a private member, so this reads it
        // without redefining access specifiers around the include.
        template <Commands CHyprCtl::* Member>
        struct SCommandsAccess {
            friend Commands& commandsOf(CHyprCtl& ctl) {
                return ctl.*Member;
            }
        };
        template struct SCommandsAccess<&CHyprCtl::m_commands>;

        using Refuse = std::move_only_function<std::string(const SError&)>;

        struct SJob {
            std::move_only_function<std::string()> work;
            // Set for work that waits for the main thread, see runWaiting().
            Refuse            refuse;
            std::thread       thread;
            std::string       reply;
            std::atomic<bool> done = false;
            // Its reply was given up on, so it is only joined once done.
            bool abandoned = false;
        };

        // A hyprctl connection waiting for the placeholders in its reply to be filled in.
        struct SRequest {
            std::string                       reply;
            std::vector<uint64_t>             jobs;
            SP<CPromiseResolver<std::string>> resolver;
        };

        bool                                      active  = false;
        int                                       eventFd = -1, stopEvent = -1;
        wl_event_source*                          source = nullptr;
        uint64_t                                  nonce = 0, nextId = 1;
        std::map<uint64_t, std::unique_ptr<SJob>> jobs;
        std::vector<SRequest>                     requests;
        int                                       batchDepth = 0, pluginCalls = 0;
        std::vector<uint64_t>                     batchJobs;
        void*                                     originalInvoke = nullptr;
        // Frames of this library that Hyprland returns into, and the reference that keeps it mapped for them after
        // an unload.
        int   frames = 0;
        void* pinned = nullptr;

        void restoreBatch();

        // Runs once Hyprland has returned from the last frame of this library that outlived an unload.
        void releasePinned() {
            wl_event_loop_add_idle(
                g_pCompositor->m_wlEventLoop,
                [](void*) {
                    if (!active)
                        restoreBatch();
                    // dlclose can unmap this library, so libwayland calls it directly instead of code in here.
                    wl_event_loop_add_idle(g_pCompositor->m_wlEventLoop, reinterpret_cast<wl_event_loop_idle_func_t>(reinterpret_cast<void (*)()>(&dlclose)),
                                           std::exchange(pinned, nullptr));
                },
                nullptr);
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

        std::string token(uint64_t id) {
            return std::format("\x1fhyprmasq-{:x}-{}\x1f", nonce, id);
        }

        void complete(SJob* job, std::string reply) {
            job->reply = std::move(reply);
            job->done.store(true, std::memory_order_release);
            eventfd_write(eventFd, 1);
        }

        uint64_t start(std::move_only_function<std::string()> work, Refuse refuse) {
            const auto id  = nextId++;
            auto*      job = (jobs[id] = std::make_unique<SJob>()).get();
            job->work      = std::move(work);
            job->refuse    = std::move(refuse);
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
            return id;
        }

        // Waits for the jobs and puts their replies in place of their placeholders. Jobs that wait for the main thread,
        // which this is, are not waited for: their replies are refused, and they stay until they are done.
        std::string collect(std::string reply, const std::vector<uint64_t>& ids) {
            for (auto id : ids) {
                std::string text = "error: hyprmasq was unloaded";
                if (auto it = jobs.find(id); it != jobs.end()) {
                    auto& job = *it->second;
                    if (job.refuse && !job.done.load(std::memory_order_acquire)) {
                        text          = job.refuse(SError{eErrorCode::UNSUPPORTED, "the request was answered before the wait ended"});
                        job.abandoned = true;
                    } else {
                        if (job.thread.joinable())
                            job.thread.join();
                        text = std::move(job.reply);
                        jobs.erase(it);
                    }
                }
                if (const auto at = reply.find(token(id)); at != std::string::npos)
                    reply.replace(at, token(id).size(), text);
            }
            return reply;
        }

        // hyprctl sends a pending promise's result instead of the returned reply, but only for requests from its
        // socket. The only other way in is HyprlandAPI::invokeHyprctlCommand, whose caller needs the reply at once.
        bool deferrable() {
            return active && pluginCalls == 0 && !g_pHyprCtl->m_currentRequestParams.pendingPromise;
        }

        // Called when the outermost request is complete.
        std::string finish(std::string reply, std::vector<uint64_t> ids) {
            if (ids.empty())
                return reply;
            if (!deferrable())
                return collect(std::move(reply), ids);
            SP<CPromiseResolver<std::string>> resolver;
            g_pHyprCtl->m_currentRequestParams.pendingPromise = CPromise<std::string>::make([&](SP<CPromiseResolver<std::string>> r) { resolver = r; });
            requests.push_back({.reply = reply, .jobs = std::move(ids), .resolver = resolver});
            return reply;
        }

        // Starts work. Outside a batch this command is the whole request, and finish() decides at once.
        std::string schedule(std::move_only_function<std::string()> work, Refuse refuse) {
            const auto id = start(std::move(work), std::move(refuse));
            if (batchDepth > 0) {
                batchJobs.push_back(id);
                return token(id);
            }
            return finish(token(id), {id});
        }

        int onEvent(int fd, uint32_t, void*) {
            eventfd_t count = 0;
            eventfd_read(fd, &count);
            std::vector<SRequest> ready;
            for (auto it = requests.begin(); it != requests.end();) {
                if (std::ranges::all_of(it->jobs, [](uint64_t id) { return !jobs.contains(id) || jobs.at(id)->done.load(std::memory_order_acquire); })) {
                    ready.push_back(std::move(*it));
                    it = requests.erase(it);
                } else
                    ++it;
            }
            for (auto& request : ready)
                request.resolver->resolve(collect(std::move(request.reply), request.jobs));
            std::erase_if(jobs, [](const auto& entry) {
                auto& job = *entry.second;
                if (!job.abandoned || !job.done.load(std::memory_order_acquire))
                    return false;
                if (job.thread.joinable())
                    job.thread.join(); // returns at once: the job is done
                return true;
            });
            return 0;
        }

        // Replaces hyprctl's batch command, which calls every command of the batch and joins their replies. Only the
        // whole batch can be deferred, once all of its commands have run.
        struct SBatch {
            std::function<std::string(eHyprCtlOutputFormat, std::string)> original;

            std::string operator()(eHyprCtlOutputFormat format, std::string request) const {
                SFrame      frame;
                std::string reply;
                ++batchDepth;
                try {
                    reply = original(format, std::move(request));
                } catch (...) {
                    if (--batchDepth == 0)
                        collect("", std::exchange(batchJobs, {}));
                    throw;
                }
                if (--batchDepth > 0)
                    return reply;
                return finish(std::move(reply), std::exchange(batchJobs, {}));
            }
        };

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

        SP<SHyprCtlCommand> batchCommand() {
            for (const auto& command : commandsOf(*g_pHyprCtl)) {
                if (command && command->name == "[[BATCH]]")
                    return command;
            }
            return nullptr;
        }

        void restoreBatch() {
            auto batch = batchCommand();
            if (const auto* wrapper = batch ? batch->fn.target<SBatch>() : nullptr) {
                auto original = wrapper->original;
                batch->fn     = std::move(original);
            }
        }
    }

    void init(void* handle) {
        auto batch = batchCommand();
        if (!batch)
            throw std::runtime_error("hyprmasq: hyprctl has no batch command");
        // HyprlandAPI::invokeHyprctlCommand has C linkage.
        originalInvoke = hook(handle, "invokeHyprctlCommand", reinterpret_cast<void*>(&invokeHyprctlCommand));
        eventFd        = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
        source         = eventFd < 0 ? nullptr : wl_event_loop_add_fd(g_pCompositor->m_wlEventLoop, eventFd, WL_EVENT_READABLE, onEvent, nullptr);
        // Wakes the jobs at the unload, as eventFd wakes the main thread when one is done.
        stopEvent = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
        if (!source || stopEvent < 0)
            throw std::runtime_error("hyprmasq: cannot watch background work");
        // Still wrapped when reloaded before a batch that unloaded it has finished.
        if (!batch->fn.target<SBatch>())
            batch->fn = SBatch{.original = batch->fn};
        nonce  = std::random_device{}();
        active = true;
    }

    void shutdown() {
        if (frames > 0) {
            // Unloaded from within a batch or a plugin's hyprctl call, which return into this library afterwards. Keep
            // it mapped, and the batch wrapper in place, until they have.
            Dl_info info{};
            if (!pinned && dladdr(reinterpret_cast<void*>(&shutdown), &info) && info.dli_fname)
                pinned = dlopen(info.dli_fname, RTLD_NOW | RTLD_NOLOAD);
        } else
            restoreBatch();
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
        for (auto& request : std::exchange(requests, {}))
            request.resolver->resolve(collect(std::move(request.reply), request.jobs));
        // Jobs of a running batch stay until the batch collects them.
        std::erase_if(jobs, [](const auto& entry) { return !std::ranges::contains(batchJobs, entry.first); });

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

    std::string run(std::move_only_function<std::string()> work) {
        if (!active || (batchDepth == 0 && !deferrable()))
            return work();
        return schedule(std::move(work), nullptr);
    }

    std::string runWaiting(std::move_only_function<std::string()> work, std::move_only_function<std::string(const SError&)> refuse) {
        if (!active || (batchDepth == 0 && !deferrable()))
            return refuse(SError{eErrorCode::UNSUPPORTED, "wait only works in requests to hyprctl's socket"});
        return schedule(std::move(work), std::move(refuse));
    }
}
