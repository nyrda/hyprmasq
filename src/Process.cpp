#include "Process.hpp"

#include <fcntl.h>
#include <unistd.h>

#include <cerrno>
#include <format>
#include <sstream>

namespace hyprmasq::process {
    namespace {
        // Without a stream: libstdc++'s filebuf throws on a read error, which a process that exits between the open and
        // the read causes, and callers on Hyprland's event loop must not throw.
        std::optional<std::string> readStat(pid_t pid) {
            const int fd = ::open(std::format("/proc/{}/stat", pid).c_str(), O_RDONLY | O_CLOEXEC);
            if (fd < 0)
                return std::nullopt;
            std::string stat;
            char        buffer[512];
            bool        failed = false;
            for (ssize_t n = 0; (n = ::read(fd, buffer, sizeof(buffer))) != 0;) {
                if (n < 0 && errno == EINTR)
                    continue;
                if (n < 0) {
                    failed = true;
                    break;
                }
                stat.append(buffer, static_cast<size_t>(n));
            }
            ::close(fd);
            return failed || stat.empty() ? std::nullopt : std::optional{stat};
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
