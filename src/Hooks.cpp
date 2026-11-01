#include "Hooks.hpp"

#include <hyprland/src/plugins/PluginAPI.hpp>

#include <cxxabi.h>
#include <dlfcn.h>
#include <cstdlib>
#include <format>
#include <ranges>
#include <stdexcept>
#include <string>
#include <vector>

namespace hyprmasq {
    namespace {
        std::vector<CFunctionHook*> hooks;

        // What a symbol demangles to. A name with C linkage stays as it is.
        std::string demangle(const char* symbol) {
            int         status = 0;
            char*       name   = abi::__cxa_demangle(symbol, nullptr, nullptr, &status);
            std::string result = status == 0 && name ? name : symbol;
            std::free(name);
            return result;
        }
    }

    void* hook(void* handle, const char* symbol, std::string_view signature, void* replacement) {
        if (const auto named = demangle(symbol); named != signature)
            throw std::runtime_error(std::format("hyprmasq: the hook of {} names {}", signature, named));
        // Hyprland's executable exports its functions. HyprlandAPI::findFunctionsByName looks them up the same way, after
        // it has run nm on the executable, which needs binutils and takes about 120 ms of the main thread. The main
        // program's own scope leaves out the plugin, which may have a copy of a function inlined from a header.
        void* program = dlopen(nullptr, RTLD_LAZY);
        void* address = program ? dlsym(program, symbol) : nullptr;
        if (program)
            dlclose(program);
        if (!address)
            throw std::runtime_error(std::format("hyprmasq: missing hook {}: the running Hyprland has no such function", signature));

        auto* created = HyprlandAPI::createFunctionHook(handle, address, replacement);
        if (!created || !created->hook())
            throw std::runtime_error(std::format("hyprmasq: cannot hook {}", signature));
        hooks.push_back(created);
        return created->m_original;
    }

    void unhookAll() {
        for (auto* created : std::views::reverse(hooks))
            created->unhook();
        hooks.clear();
    }
}
