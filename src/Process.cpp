#include "Process.hpp"

#include <format>
#include <fstream>
#include <iterator>
#include <sstream>

namespace hyprmasq::process {
    namespace {
        // Environments past this size are cut there; the variables hyprmasq reads come early or not at all.
        constexpr std::streamsize  MAX_ENVIRONMENT = 1 << 20;

        std::optional<std::string> readStat(pid_t pid) {
            std::ifstream file(std::format("/proc/{}/stat", pid));
            if (!file)
                return std::nullopt;
            std::string stat((std::istreambuf_iterator<char>(file)), {});
            return stat.empty() ? std::nullopt : std::optional{stat};
        }

        // The fields after the command name, which is in parentheses and may contain spaces and parentheses itself.
        std::optional<std::istringstream> statFields(pid_t pid) {
            const auto stat = readStat(pid);
            if (!stat)
                return std::nullopt;
            const auto close = stat->rfind(')');
            if (close == std::string::npos || close + 2 > stat->size())
                return std::nullopt;
            return std::istringstream(stat->substr(close + 2));
        }
    }

    pid_t parent(pid_t pid) {
        if (pid <= 0)
            return 0;
        auto fields = statFields(pid);
        if (!fields)
            return 0;
        std::string state;
        pid_t       ppid = 0;
        *fields >> state >> ppid;
        return *fields ? ppid : 0;
    }

    std::optional<unsigned long long> started(pid_t pid) {
        auto fields = statFields(pid);
        if (!fields)
            return std::nullopt;
        // starttime is the 22nd field; the fields read here start with the 3rd.
        std::string field;
        for (int i = 3; i < 22 && *fields >> field; ++i) {}
        unsigned long long ticks = 0;
        *fields >> ticks;
        return *fields ? std::optional{ticks} : std::nullopt;
    }

}
