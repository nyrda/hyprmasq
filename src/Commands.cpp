#include "Commands.hpp"

#include "Deferred.hpp"
#include "Eis.hpp"
#include "Screenshot.hpp"
#include "Windows.hpp"

#include <charconv>
#include <format>
#include <optional>
#include <sstream>
#include <vector>

namespace hyprmasq {
    namespace {
        constexpr std::string_view USAGE = "usage:\n"
                                           "  hyprctl hyprmasq screenshot <window-address> <absolute-path.png> [x y width height]\n"
                                           "  hyprctl hyprmasq eis open <window-address> [force]\n"
                                           "  hyprctl hyprmasq eis close <window-address>\n"
                                           "  hyprctl hyprmasq eis list\n";

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

        std::string error(eHyprCtlOutputFormat format, std::string_view message) {
            if (format == FORMAT_JSON)
                return std::format(R"({{"ok":false,"error":{}}})", jsonString(message));
            return std::format("error: {}", message);
        }

        std::optional<int> parseInt(const std::string& text) {
            int        value     = 0;
            const auto end       = text.data() + text.size();
            const auto [ptr, ec] = std::from_chars(text.data(), end, value);
            if (ec != std::errc{} || ptr != end)
                return std::nullopt;
            return value;
        }

        std::string screenshot(eHyprCtlOutputFormat format, const std::vector<std::string>& args) {
            if (args.size() != 2 && args.size() != 6)
                return error(format, USAGE);

            std::optional<SRegion> region;
            if (args.size() == 6) {
                std::optional<int> v[4];
                for (size_t i = 0; i < 4; ++i)
                    v[i] = parseInt(args[2 + i]);
                if (!v[0] || !v[1] || !v[2] || !v[3])
                    return error(format, "the region is x y width height in whole logical pixels");
                region = SRegion{*v[0], *v[1], *v[2], *v[3]};
            }

            const auto window = windowFromAddress(args[0]);
            if (!window)
                return error(format, std::format("no mapped window {}", args[0]));

            const std::filesystem::path path = args[1];
            if (!path.is_absolute())
                return error(format, "output path must be absolute");

            auto capture = captureWindow(window, region);
            if (!capture)
                return error(format, capture.error());

            // Rendering needs the compositor, encoding does not.
            return deferred::run([format, path, capture = std::move(*capture)] mutable -> std::string {
                if (auto written = writePNG(capture, path); !written)
                    return error(format, written.error());
                if (format == FORMAT_JSON) {
                    const auto& r = capture.region;
                    return std::format(R"({{"ok":true,"path":{},"width":{},"height":{},"scale":{},"region":{{"x":{},"y":{},"width":{},"height":{}}}}})", jsonString(path.string()),
                                       capture.width, capture.height, capture.scale, r.x, r.y, r.width, r.height);
                }
                return "ok";
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

        if (words[1] == "eis") {
            if (args.size() == 1 && args[0] == "list") {
                std::string out;
                for (const auto& info : listEis()) {
                    if (format == FORMAT_JSON) {
                        if (!out.empty())
                            out += ",";
                        out += std::format(R"({{"address":{},"path":{},"force":{}}})", jsonString(info.address), jsonString(info.path), info.force);
                    } else
                        out += info.address + " " + info.path + (info.force ? " force" : "") + "\n";
                }
                return format == FORMAT_JSON ? "[" + out + "]" : out;
            }
            // A plain word, because hyprctl takes every argument that starts with a dash as its own flag.
            if (!args.empty() && args[0] == "open" && (args.size() == 2 || (args.size() == 3 && args[2] == "force"))) {
                const auto window = windowFromAddress(args[1]);
                if (!window)
                    return error(format, "no mapped window " + args[1]);
                const auto result = openEis(window, args.size() == 3);
                if (!result)
                    return error(format, result.error());
                return format == FORMAT_JSON ? std::format(R"({{"ok":true,"path":{}}})", jsonString(*result)) : *result;
            }
            if (args.size() != 2)
                return error(format, USAGE);
            if (args[0] == "close") {
                if (!closeEis(args[1]))
                    return error(format, "no open socket for " + args[1]);
                return format == FORMAT_JSON ? R"({"ok":true})" : "ok";
            }
            return error(format, USAGE);
        }

        return error(format, std::format("unknown subcommand {}\n{}", words[1], USAGE));
    }
}
