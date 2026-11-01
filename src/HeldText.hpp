#pragma once

#include <hyprland/src/protocols/types/DataDevice.hpp>

#include <memory>
#include <string>
#include <vector>

// A text that the plugin holds as a selection source of its own, for the agent's paste: it writes the text into the
// pipe of each paste. Pipes never block the main thread: a write that the pipe does not take at once waits on Hyprland's
// event loop, and gives up after a timeout.
namespace hyprmasq::heldtext {
    // The pastes that asked a held text for its data, those of them whose writes have ended, and those of these that got
    // all of it, not cut off by a reader that closed its pipe, an error or the timeout.
    struct SReads {
        size_t asked = 0, ended = 0, complete = 0;
    };

    class CHeldText : public IDataSource {
      public:
        explicit CHeldText(std::string text);
        SReads reads() const;
        std::vector<std::string> mimes() override;
        void send(const std::string& mime, Hyprutils::OS::CFileDescriptor fd) override;
        void accepted(const std::string& mime) override {}
        void cancelled() override {}
        void error(uint32_t code, const std::string& msg) override {}

      private:
        std::shared_ptr<const std::string> m_text;
        std::shared_ptr<SReads>            m_reads = std::make_shared<SReads>();
    };

    // Ends every write to a paste's pipe. Call before the plugin unloads, after the sources are gone.
    void shutdown();
}
