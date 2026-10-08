#include "Commands.hpp"

#include "Deferred.hpp"
#include "Eis.hpp"
#include "Error.hpp"
#include "Launch.hpp"
#include "Screenshot.hpp"
#include "Wait.hpp"
#include "Windows.hpp"

#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/output/Monitor.hpp>

#include <charconv>
#include <cmath>
#include <format>
#include <optional>
#include <set>
#include <sstream>
#include <vector>

namespace hyprmasq {
    namespace {
        constexpr std::string_view USAGE = "usage:\n"
                                           "  hyprctl hyprmasq screenshot <window-address> <absolute-path.png> [x y width height] [max pixels] [popups]\n"
                                           "  hyprctl hyprmasq wait <window-address> [quiet-ms [timeout-ms]]\n"
                                           "  hyprctl hyprmasq wait <window-address> since <commits> [expect ms] [quiet ms] [deadline ms] [id word]\n"
                                           "  hyprctl hyprmasq cancel <word>\n"
                                           "  hyprctl hyprmasq state <window-address>\n"
                                           "  hyprctl hyprmasq launch <workspace-hex> <token> <shell-command-line-hex>\n"
                                           "  hyprctl hyprmasq launched\n"
                                           "  hyprctl hyprmasq lease <window-address> <ms> <owner>\n"
                                           "  hyprctl hyprmasq lease <window-address> release [owner]\n"
                                           "  hyprctl hyprmasq eis open <window-address> [force]\n"
                                           "  hyprctl hyprmasq eis close <window-address>\n"
                                           "  hyprctl hyprmasq eis list\n"
                                           "  hyprctl hyprmasq version\n";

        std::string                jsonString(std::string_view in) {
            std::string out = "\"";
            for (const char c : in) {
                switch (c) {
                    case '"': out += "\\\""; break;
                    case '\\': out += "\\\\"; break;
                    case '\n': out += "\\n"; break;
                    default:
                        if (sc<unsigned char>(c) < 0x20)
                            out += std::format("\\u{:04x}", c);
                        else
                            out += c;
                }
            }
            return out + "\"";
        }

        template <typename T>
        std::string jsonOrNull(const std::optional<T>& value) {
            return value ? std::format("{}", *value) : "null";
        }

        std::string error(eHyprCtlOutputFormat format, const SError& e) {
            if (format == FORMAT_JSON)
                return std::format(R"({{"ok":false,"error":{},"code":"{}"}})", jsonString(e.message), codeName(e.code));
            return std::format("error: {}", e.message);
        }

        std::string error(eHyprCtlOutputFormat format, eErrorCode code, std::string_view message) {
            return error(format, SError{code, std::string{message}});
        }

        std::string usage(eHyprCtlOutputFormat format) {
            return error(format, eErrorCode::INVALID_ARGUMENTS, USAGE);
        }

        std::string windowGone(eHyprCtlOutputFormat format, std::string_view address) {
            return error(format, eErrorCode::WINDOW_GONE, std::format("no mapped window {}", address));
        }

        template <typename T>
        std::optional<T> parseNumber(const std::string& text) {
            T          value     = 0;
            const auto end       = text.data() + text.size();
            const auto [ptr, ec] = std::from_chars(text.data(), end, value);
            if (ec != std::errc{} || ptr != end)
                return std::nullopt;
            return value;
        }

        // Work that throws still answers in the requested format.
        std::move_only_function<std::string()> answering(eHyprCtlOutputFormat format, std::move_only_function<std::string()> work) {
            return [format, work = std::move(work)] mutable -> std::string {
                try {
                    return work();
                } catch (const std::exception& e) { return error(format, eErrorCode::INTERNAL, e.what()); }
            };
        }

        std::string defer(eHyprCtlOutputFormat format, std::move_only_function<std::string()> work) {
            return deferred::run(answering(format, std::move(work)));
        }

        // The reply of a wait waits for the main thread.
        std::string deferWait(eHyprCtlOutputFormat format, std::move_only_function<std::string()> work) {
            return deferred::runWaiting(answering(format, std::move(work)), [format](const SError& e) { return error(format, e); });
        }

        std::string screenshot(eHyprCtlOutputFormat format, const std::vector<std::string>& args) {
            if (args.size() < 2)
                return usage(format);

            SCaptureOptions options;
            size_t          next = 2;
            if (args.size() > next && args[next] != "max" && args[next] != "popups") {
                if (args.size() < next + 4)
                    return usage(format);
                std::optional<int> v[4];
                for (size_t i = 0; i < 4; ++i)
                    v[i] = parseNumber<int>(args[next + i]);
                if (!v[0] || !v[1] || !v[2] || !v[3])
                    return error(format, eErrorCode::INVALID_ARGUMENTS, "the region is x y width height in whole logical pixels");
                options.region = SRegion{*v[0], *v[1], *v[2], *v[3]};
                next += 4;
            }
            std::set<std::string> seen;
            for (; next < args.size(); ++next) {
                if (!seen.insert(args[next]).second)
                    return usage(format);
                if (args[next] == "popups")
                    options.popups = true;
                else if (args[next] == "max" && next + 1 < args.size()) {
                    const auto pixels = parseNumber<int>(args[++next]);
                    if (!pixels || *pixels < 1)
                        return error(format, eErrorCode::INVALID_ARGUMENTS, "max needs a whole number of pixels, at least 1");
                    options.maxPixels = *pixels;
                } else
                    return usage(format);
            }

            const auto window = windowFromAddress(args[0]);
            if (!window)
                return windowGone(format, args[0]);

            const std::filesystem::path path = args[1];
            if (!path.is_absolute())
                return error(format, eErrorCode::INVALID_ARGUMENTS, "output path must be absolute");

            auto capture = captureWindow(window, options);
            if (!capture)
                return error(format, capture.error());
            // Read as the window was rendered, so that a wait from this value counts every commit the image lacks.
            const auto socket   = socketState(window);
            const auto commits  = socket ? std::optional{socket->content->commits} : std::nullopt;
            const auto socketId = socket ? jsonString(socket->id) : "null";
            // The image then shows a frame of another size than the window's.
            const bool behind = resizing(window);

            // Rendering needs the compositor, encoding does not.
            return defer(format, [format, path, commits, socketId, behind, capture = std::move(*capture)] mutable -> std::string {
                if (auto written = writePNG(capture, path); !written)
                    return error(format, eErrorCode::INTERNAL, written.error());
                if (format == FORMAT_JSON) {
                    const auto& r = capture.region;
                    return std::format(
                        R"({{"ok":true,"path":{},"width":{},"height":{},"scale":{},"region":{{"x":{},"y":{},"width":{},"height":{}}},"commits":{},"socketId":{},"resizing":{}}})",
                        jsonString(path.string()), capture.width, capture.height, capture.scale, r.x, r.y, r.width, r.height, jsonOrNull(commits), socketId, behind);
                }
                return "ok";
            });
        }

        // The members of the lease object, also the reply to `lease`.
        std::string leaseFields(const SLeaseState& lease) {
            return std::format(R"("held":true,"remainingMs":{},"owner":{})", lease.remainingMs, jsonString(lease.owner));
        }

        std::string leaseText(const SLeaseState& lease) {
            return std::format("{} ms by {}", lease.remainingMs, lease.owner);
        }

        // Bytes written as pairs of hex digits, or none when the text is not that. Requests carry text from elsewhere
        // that way, since hyprctl and Hyprland look for words such as --batch, /instances and rollinglog anywhere in a
        // request.
        std::optional<std::string> fromHex(std::string_view hex) {
            if (hex.size() % 2 != 0)
                return std::nullopt;
            std::string out;
            out.reserve(hex.size() / 2);
            for (size_t i = 0; i < hex.size(); i += 2) {
                unsigned char byte   = 0;
                const auto [end, ec] = std::from_chars(hex.data() + i, hex.data() + i + 2, byte, 16);
                if (ec != std::errc{} || end != hex.data() + i + 2)
                    return std::nullopt;
                out.push_back(sc<char>(byte));
            }
            return out;
        }

        // launch <workspace> <token> <command line>, with the workspace and the command line in hex.
        std::string launchCommand(eHyprCtlOutputFormat format, const std::vector<std::string>& args) {
            if (args.size() != 3)
                return usage(format);
            const auto workspace = fromHex(args[0]);
            const auto line      = fromHex(args[2]);
            if (!workspace || !line)
                return error(format, eErrorCode::INVALID_ARGUMENTS, "the workspace and the command line must be written in hex");
            const auto pid = launch::start(*workspace, args[1], *line);
            if (!pid)
                return error(format, pid.error());
            return format == FORMAT_JSON ? std::format(R"({{"ok":true,"pid":{}}})", *pid) : std::format("launched pid {}", *pid);
        }

        std::string launched(eHyprCtlOutputFormat format, const std::vector<std::string>& args) {
            if (!args.empty())
                return usage(format);
            std::string out;
            for (const auto& w : launch::windows()) {
                if (format == FORMAT_JSON)
                    out +=
                        std::format(R"({}{{"address":{},"stableId":{},"token":{}}})", out.empty() ? "" : ",", jsonString(w.address), jsonString(w.stableId), jsonString(w.token));
                else
                    out += std::format("{} {}\n", w.address, w.token);
            }
            if (format == FORMAT_JSON)
                return R"({"ok":true,"windows":[)" + out + "]}";
            // hyprctl prints "unknown request" for an empty reply.
            return out.empty() ? "no launched windows" : out;
        }

        std::string state(eHyprCtlOutputFormat format, const std::vector<std::string>& args) {
            if (args.size() != 1)
                return usage(format);
            const auto window = windowFromAddress(args[0]);
            if (!window)
                return windowGone(format, args[0]);

            const auto             socket   = socketState(window);
            const auto             caps     = capabilities(window);
            const auto             size     = window->size(Desktop::View::IGeometric::GEOMETRIC_GOAL);
            const auto             monitor  = window->m_monitor.lock();
            const auto             scale    = monitor ? std::optional<double>{monitor->m_scale} : std::nullopt;
            const auto             commits  = socket ? std::optional{socket->content->commits} : std::nullopt;
            const auto             lease    = socket ? socket->lease : std::nullopt;
            const std::string_view keyboard = caps.x11Focus ? "x11-focus" : "yes";
            const bool             behind   = resizing(window);
            if (format == FORMAT_JSON)
                return std::format(R"({{"ok":true,"address":{},"stableId":"{:x}","xwayland":{},"size":{{"width":{},"height":{}}},"resizing":{},"scale":{},"socket":{},)"
                                   R"("socketId":{},"commits":{},"lease":{},"capabilities":{{"lease":{},"hold":{},"dnd":{},"keyboard":"{}"}}}})",
                                   jsonString(windowAddress(window)), window->m_stableID, window->m_isX11, sc<int>(size.x), sc<int>(size.y), behind, jsonOrNull(scale),
                                   socket ? jsonString(socket->path) : "null", socket ? jsonString(socket->id) : "null", jsonOrNull(commits),
                                   lease ? "{" + leaseFields(*lease) + "}" : "null", caps.lease, caps.hold, caps.dnd, keyboard);
            return std::format("address: {}\nstableId: {:x}\nxwayland: {}\nsize: {}x{}\nresizing: {}\nscale: {}\nsocket: {}\nsocketId: {}\ncommits: {}\nlease: {}\n"
                               "capabilities: lease={} hold={} dnd={} keyboard={}\n",
                               windowAddress(window), window->m_stableID, window->m_isX11, sc<int>(size.x), sc<int>(size.y), behind, jsonOrNull(scale),
                               socket ? socket->path : "none", socket ? socket->id : "none", commits ? std::to_string(*commits) : "none", lease ? leaseText(*lease) : "none",
                               caps.lease, caps.hold, caps.dnd, keyboard);
        }

        constexpr int MAX_LEASE_MS = 600000;

        std::string   leaseCommand(eHyprCtlOutputFormat format, const std::vector<std::string>& args) {
            const bool release = args.size() >= 2 && args[1] == "release";
            if (release ? args.size() > 3 : args.size() != 3)
                return usage(format);
            const auto ms = release ? std::nullopt : parseNumber<int>(args[1]);
            if (!release && (!ms || *ms < 1 || *ms > MAX_LEASE_MS))
                return error(format, eErrorCode::INVALID_ARGUMENTS, std::format("a lease lasts 1 to {} milliseconds", MAX_LEASE_MS));
            const auto window = windowFromAddress(args[0]);
            if (!window)
                return windowGone(format, args[0]);

            if (release) {
                const bool released = releaseLease(window, args.size() == 3 ? std::optional{args[2]} : std::nullopt);
                if (format == FORMAT_JSON)
                    return std::format(R"({{"ok":true,"released":{}}})", released);
                return released ? "released" : "no lease released";
            }
            const auto lease = holdLease(window, *ms, args[2]);
            if (!lease)
                return error(format, lease.error());
            return format == FORMAT_JSON ? "{\"ok\":true," + leaseFields(*lease) + "}" : "held " + leaseText(*lease);
        }
    }

    namespace {
        // hyprctl stops reading a reply after 5 seconds.
        constexpr int MAX_WAIT_MS = 4500;
        // Clients that talk to Hyprland's socket themselves wait as long as the reply takes.
        constexpr int MAX_CHANGE_WAIT_MS = 60000;

        std::string   changeWait(eHyprCtlOutputFormat format, const PHLWINDOW& window, const std::vector<std::string>& args) {
            wait::SChangeOptions  options;
            std::set<std::string> seen;
            for (size_t i = 1; i < args.size(); i += 2) {
                const auto& key = args[i];
                if (i + 1 >= args.size() || !seen.insert(key).second)
                    return usage(format);
                const auto& value = args[i + 1];
                if (key == "since") {
                    const auto since = parseNumber<uint64_t>(value);
                    if (!since)
                        return error(format, eErrorCode::INVALID_ARGUMENTS, "since needs a commit counter value, a whole number of at least 0");
                    options.since = *since;
                } else if (key == "id")
                    options.id = value;
                else if (key == "expect" || key == "quiet" || key == "deadline") {
                    const auto ms = parseNumber<int>(value);
                    if (!ms || *ms < 1 || *ms > MAX_CHANGE_WAIT_MS)
                        return error(format, eErrorCode::INVALID_ARGUMENTS, std::format("{} needs 1 to {} milliseconds", key, MAX_CHANGE_WAIT_MS));
                    if (key == "expect")
                        options.expectMs = *ms;
                    else if (key == "quiet")
                        options.quietMs = *ms;
                    else
                        options.deadlineMs = *ms;
                } else
                    return usage(format);
            }
            // The reply waits for the main thread, which a caller that needs it at once would block.
            if (!deferred::canDefer())
                return error(format, eErrorCode::UNSUPPORTED, "wait only works in requests to hyprctl's socket");

            auto future = wait::startChange(window, options);
            if (!future)
                return error(format, future.error());
            return deferWait(format, [format, future = std::move(*future)] mutable -> std::string {
                const auto result = future.get();
                if (result.error)
                    return error(format, *result.error);
                if (format == FORMAT_JSON) {
                    // The whole window-local logical pixels that cover each animation.
                    std::string animated;
                    for (const auto& box : result.animated) {
                        const int x = sc<int>(std::floor(box.x)), y = sc<int>(std::floor(box.y));
                        animated += std::format(R"({}{{"x":{},"y":{},"width":{},"height":{}}})", animated.empty() ? "" : ",", x, y, sc<int>(std::ceil(box.x + box.w)) - x,
                                                  sc<int>(std::ceil(box.y + box.h)) - y);
                    }
                    return std::format(
                        R"({{"ok":true,"ended":"{}","commits":{},"socketId":{},"redrawObserved":{},"commitsSince":{},"quietMs":{},"elapsedMs":{},"resizing":{},"animated":[{}]}})",
                        result.ended, result.commits, jsonString(result.socketId), result.commitsSince > 0, result.commitsSince, result.quietMs, result.elapsedMs, result.resizing,
                        animated);
                }
                return std::string{result.ended};
            });
        }

        std::string waitCommand(eHyprCtlOutputFormat format, const std::vector<std::string>& args) {
            const bool change = args.size() > 1 && args[1] == "since";
            if (args.empty() || (!change && args.size() > 3))
                return usage(format);
            const auto window = windowFromAddress(args[0]);
            if (!window)
                return windowGone(format, args[0]);
            if (change)
                return changeWait(format, window, args);

            const auto quiet   = args.size() > 1 ? parseNumber<int>(args[1]) : std::optional<int>{200};
            const auto timeout = args.size() > 2 ? parseNumber<int>(args[2]) : std::optional<int>{3000};
            if (!quiet || !timeout || *quiet < 1 || *timeout < *quiet || *timeout > MAX_WAIT_MS)
                return error(format, eErrorCode::INVALID_ARGUMENTS, std::format("quiet-ms must be at least 1, and timeout-ms at least quiet-ms and at most {}", MAX_WAIT_MS));
            // The reply waits for the main thread, which a caller that needs it at once would block.
            if (!deferred::canDefer())
                return error(format, eErrorCode::UNSUPPORTED, "wait only works in requests to hyprctl's socket");

            auto future = wait::start(window, *quiet, *timeout);
            if (!future)
                return error(format, future.error());
            return deferWait(format, [format, future = std::move(*future)] mutable -> std::string {
                const auto result = future.get();
                if (result.error)
                    return error(format, *result.error);
                if (format == FORMAT_JSON)
                    return std::format(R"({{"ok":true,"settled":{},"waited":{},"frames":{},"framesSinceInput":{}}})", result.settled, result.waitedMs, result.frames,
                                       jsonOrNull(result.framesSinceInput));
                return result.settled ? "settled" : "timeout";
            });
        }
    }

    std::string handleCommand(eHyprCtlOutputFormat format, std::string request) {
        std::istringstream       stream(request);
        std::vector<std::string> words;
        for (std::string word; stream >> word;)
            words.push_back(word);

        // words[0] is the command name itself.
        if (words.size() < 2 || words[1] == "help")
            return std::string{USAGE};

        const std::vector<std::string> args(words.begin() + 2, words.end());
        if (words[1] == "screenshot")
            return screenshot(format, args);
        if (words[1] == "wait")
            return waitCommand(format, args);
        if (words[1] == "state")
            return state(format, args);
        if (words[1] == "launched")
            return launched(format, args);
        if (words[1] == "launch")
            return launchCommand(format, args);
        if (words[1] == "lease")
            return leaseCommand(format, args);

        if (words[1] == "cancel") {
            if (args.size() != 1)
                return usage(format);
            const bool cancelled = wait::cancel(args[0]);
            if (format == FORMAT_JSON)
                return std::format(R"({{"ok":true,"cancelled":{}}})", cancelled);
            return cancelled ? "cancelled" : "no pending wait";
        }

        if (words[1] == "version") {
            if (!args.empty())
                return usage(format);
            if (format == FORMAT_JSON)
                return std::format(R"({{"ok":true,"version":"{}","interface":{}}})", VERSION, INTERFACE);
            return std::format("hyprmasq {} interface {}", VERSION, INTERFACE);
        }

        if (words[1] == "eis") {
            if (args.size() == 1 && args[0] == "list") {
                std::string out;
                for (const auto& info : listEis()) {
                    if (format == FORMAT_JSON) {
                        if (!out.empty())
                            out += ",";
                        out +=
                            std::format(R"({{"address":{},"path":{},"socketId":{},"force":{}}})", jsonString(info.address), jsonString(info.path), jsonString(info.id), info.force);
                    } else
                        out += info.address + " " + info.path + (info.force ? " force" : "") + "\n";
                }
                return format == FORMAT_JSON ? "[" + out + "]" : out;
            }
            // A plain word, because hyprctl takes every argument that starts with a dash as its own flag.
            if (!args.empty() && args[0] == "open" && (args.size() == 2 || (args.size() == 3 && args[2] == "force"))) {
                const auto window = windowFromAddress(args[1]);
                if (!window)
                    return windowGone(format, args[1]);
                const auto result = openEis(window, args.size() == 3);
                if (!result)
                    return error(format, result.error());
                return format == FORMAT_JSON ? std::format(R"({{"ok":true,"path":{},"socketId":{}}})", jsonString(result->path), jsonString(result->id)) : result->path;
            }
            if (args.size() != 2)
                return usage(format);
            if (args[0] == "close") {
                if (!closeEis(args[1]))
                    return error(format, eErrorCode::NO_SOCKET, "no open socket for " + args[1]);
                return format == FORMAT_JSON ? R"({"ok":true})" : "ok";
            }
            return usage(format);
        }

        return error(format, eErrorCode::INVALID_ARGUMENTS, std::format("unknown subcommand {}\n{}", words[1], USAGE));
    }
}
