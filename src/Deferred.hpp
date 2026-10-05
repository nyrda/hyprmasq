#pragma once
#include "Error.hpp"
#include <functional>
#include <string>

// hyprctl replies that are computed on a background thread, so Hyprland keeps running while they are.
namespace hyprmasq::deferred {
    void init(void* handle);
    // Waits for all running work and answers every client that is still waiting.
    void shutdown();

    // Runs work on its own thread. Returns the string for the command handler to return: a placeholder when the reply
    // is sent once work finishes, or work's own result when the caller needs the reply at once.
    std::string run(std::move_only_function<std::string()> work);
    // Like run(), for work that waits for the main thread, which therefore never runs it or waits for it. Where run()
    // would, the reply is refuse(why) instead, and the work runs on unanswered until it ends.
    std::string runWaiting(std::move_only_function<std::string()> work, std::move_only_function<std::string(const SError&)> refuse);
    // Whether run() sends the reply later rather than running work at once on the calling thread. Work that waits for
    // the main thread may only start when this is true.
    bool canDefer();
}
