#include "Hooks.hpp"

#include <hyprland/src/plugins/PluginAPI.hpp>

#include <format>
#include <ranges>
#include <stdexcept>
#include <vector>

namespace hyprmasq {
    namespace {
        std::vector<CFunctionHook*> hooks;
    }

    void* hook(void* handle, std::string_view qualifiedName, void* replacement) {
        const auto                  separator = qualifiedName.rfind("::");
        const auto                  name      = std::string(separator == std::string_view::npos ? qualifiedName : qualifiedName.substr(separator + 2));
        const auto                  prefix    = std::string(qualifiedName) + "(";

        std::vector<SFunctionMatch> matches;
        for (auto& match : HyprlandAPI::findFunctionsByName(handle, name)) {
            // Compilers split cold paths into "[clone .cold]" symbols, which are not entry points.
            if (match.demangled.starts_with(prefix) && !match.demangled.contains("[clone"))
                matches.push_back(match);
        }
        if (matches.size() != 1)
            throw std::runtime_error(std::format("hyprmasq: {} hook {}", matches.empty() ? "missing" : "ambiguous", qualifiedName));

        auto* created = HyprlandAPI::createFunctionHook(handle, matches[0].address, replacement);
        if (!created || !created->hook())
            throw std::runtime_error(std::format("hyprmasq: cannot hook {}", qualifiedName));
        hooks.push_back(created);
        return created->m_original;
    }

    void unhookAll() {
        for (auto* created : std::views::reverse(hooks))
            created->unhook();
        hooks.clear();
    }
}
