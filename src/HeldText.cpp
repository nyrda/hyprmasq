#include "HeldText.hpp"

#include <hyprland/src/Compositor.hpp>

#include <algorithm>
#include <array>
#include <cerrno>
#include <fcntl.h>
#include <list>
#include <unistd.h>

namespace hyprmasq::heldtext {
    using Hyprutils::OS::CFileDescriptor;

    namespace {
        // How long a paste may take to read the text.
        constexpr int SEND_MS = 30000;

        // The types that apps ask text for, best first.
        constexpr std::array TEXT_TYPES{"text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "STRING", "TEXT"};

        // A paste's pipe that still takes text.
        struct SWrite {
            CFileDescriptor                    fd;
            std::shared_ptr<const std::string> text;
            std::shared_ptr<SReads>            reads;
            size_t                             offset = 0;
            wl_event_source*                   io     = nullptr;
            wl_event_source*                   timer  = nullptr;
        };
        std::list<std::unique_ptr<SWrite>> writes;

        void endWrite(SWrite* w, bool complete) {
            ++w->reads->ended;
            if (complete)
                ++w->reads->complete;
            if (w->io)
                wl_event_source_remove(w->io);
            if (w->timer)
                wl_event_source_remove(w->timer);
            std::erase_if(writes, [w](const auto& p) { return p.get() == w; });
        }

        enum class ePump : uint8_t {
            DONE,   // everything is written
            AGAIN,  // the pipe takes no more now
            FAILED, // the reader is gone, or the write failed
        };

        // Writes what the pipe takes now.
        ePump pump(SWrite& w) {
            while (w.offset < w.text->size()) {
                const auto n = write(w.fd.get(), w.text->data() + w.offset, w.text->size() - w.offset);
                if (n > 0) {
                    w.offset += sc<size_t>(n);
                    continue;
                }
                if (n < 0 && errno == EINTR)
                    continue;
                return n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) ? ePump::AGAIN : ePump::FAILED;
            }
            return ePump::DONE;
        }
    }

    CHeldText::CHeldText(std::string text) : m_text(std::make_shared<const std::string>(std::move(text))) {}

    SReads CHeldText::reads() const {
        return *m_reads;
    }

    std::vector<std::string> CHeldText::mimes() {
        return {TEXT_TYPES.begin(), TEXT_TYPES.end()};
    }

    void CHeldText::send(const std::string& mime, CFileDescriptor fd) {
        if (!fd.isValid() || !std::ranges::contains(TEXT_TYPES, mime))
            return; // closing the pipe ends the paste with no data
        const int flags = fcntl(fd.get(), F_GETFL);
        if (flags < 0 || fcntl(fd.get(), F_SETFL, flags | O_NONBLOCK) < 0)
            return;
        ++m_reads->asked;
        auto w = std::make_unique<SWrite>(SWrite{.fd = std::move(fd), .text = m_text, .reads = m_reads});
        if (const auto result = pump(*w); result != ePump::AGAIN) {
            ++m_reads->ended;
            if (result == ePump::DONE)
                ++m_reads->complete;
            return;
        }
        auto* raw = w.get();
        writes.push_back(std::move(w));
        raw->io = wl_event_loop_add_fd(
            g_pCompositor->m_wlEventLoop, raw->fd.get(), WL_EVENT_WRITABLE,
            [](int, uint32_t mask, void* data) {
                auto* w = sc<SWrite*>(data);
                if (mask & (WL_EVENT_HANGUP | WL_EVENT_ERROR)) {
                    endWrite(w, false);
                    return 0;
                }
                if (const auto result = pump(*w); result != ePump::AGAIN)
                    endWrite(w, result == ePump::DONE);
                return 0;
            },
            raw);
        raw->timer = wl_event_loop_add_timer(
            g_pCompositor->m_wlEventLoop,
            [](void* data) {
                endWrite(sc<SWrite*>(data), false);
                return 0;
            },
            raw);
        if (!raw->io || !raw->timer) {
            endWrite(raw, false);
            return;
        }
        wl_event_source_timer_update(raw->timer, SEND_MS);
    }

    void shutdown() {
        while (!writes.empty())
            endWrite(writes.front().get(), false);
    }
}
