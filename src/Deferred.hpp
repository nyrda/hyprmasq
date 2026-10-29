#pragma once
#include "Error.hpp"
#include <hyprland/src/ipc/s1/S1.hpp>
#include <functional>
#include <string>

// hyprctl replies that are computed on a background thread, so Hyprland keeps running while they are.
namespace hyprmasq::deferred {
    void init(void* handle);
    // Signals stopFd(), waits for all running work and answers every client that is still waiting.
    void shutdown();

    // Runs work on its own thread. Returns the reply for the command handler to return: a promise that hyprctl answers
    // with once work finishes, or work's own result when the caller needs the reply at once.
    IPC::Socket1::SResponse run(std::move_only_function<std::string()> work);
    // Like run(), for work that waits for the main thread, which therefore never runs it or waits for it. Where run()
    // would run work at once, the reply is refuse(why) instead.
    IPC::Socket1::SResponse runWaiting(std::move_only_function<std::string()> work, std::move_only_function<std::string(const SError&)> refuse);
    // Whether run() sends the reply later rather than running work at once on the calling thread. Work that waits for
    // the main thread may only start when this is true.
    bool canDefer();
    // Whether the main thread waits for the work that calls this to end: work on the main thread itself, which run()
    // runs there when it cannot defer the reply, as for HyprlandAPI::invokeHyprctlCommand, or cannot start a thread.
    // Such work must not wait for anything outside Hyprland, such as a pipe's reader.
    bool waited();
    // An eventfd that becomes readable when the plugin unloads, or -1 while it is not loaded. shutdown() waits for every
    // job, so work that waits for something outside Hyprland, such as a pipe's reader, watches it and ends early. It
    // stays open until every job that may have read it has ended.
    int stopFd();
}
