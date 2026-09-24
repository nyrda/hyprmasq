#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/debug/log/Logger.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/render/OpenGL.hpp>
#define protected public
#include <hyprland/src/render/Renderer.hpp>
#undef protected
#include <hyprland/src/render/gl/GLFramebuffer.hpp>
#include <hyprland/src/render/types.hpp>
#include <hyprland/src/helpers/time/Time.hpp>
#include <hyprutils/math/Region.hpp>
#include <hyprutils/utils/ScopeGuard.hpp>

#include <GLES3/gl32.h>
#include <png.h>
#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <filesystem>
#include <format>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

using namespace Hyprutils::Math;

static HANDLE PHANDLE = nullptr;

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

namespace {
    constexpr std::string_view HELP = "usage: hyprctl hyprmasq screenshot <window-address> <output.png>";

    struct SRequest {
        std::string action;
        std::string windowAddress;
        std::string outputPath;
    };

    std::vector<std::string> splitWords(const std::string& request) {
        std::istringstream       stream(request);
        std::vector<std::string> words;
        std::string              word;

        while (stream >> word) {
            words.push_back(word);
        }

        return words;
    }

    std::optional<SRequest> parseRequest(const std::string& request, std::string& error) {
        const auto words = splitWords(request);
        if (words.size() == 1) {
            error = std::string{HELP};
            return std::nullopt;
        }

        if (words.size() != 4 || words[0] != "hyprmasq" || words[1] != "screenshot") {
            error = std::string{HELP};
            return std::nullopt;
        }

        return SRequest{.action = words[1], .windowAddress = words[2], .outputPath = words[3]};
    }

    PHLWINDOW windowFromAddress(std::string_view address) {
        if (address.starts_with("address:"))
            address.remove_prefix(8);

        for (const auto& window : g_pCompositor->m_windows) {
            if (!window)
                continue;

            const auto current = std::format("0x{:x}", rc<uintptr_t>(window.get()));
            if (current == address)
                return window;
        }

        return nullptr;
    }

    bool writePNG(const std::filesystem::path& path, int width, int height, const std::vector<uint8_t>& rgba, std::string& error) {
        const auto pathString = path.string();
        auto*      file       = std::fopen(pathString.c_str(), "wb");
        if (!file) {
            error = std::format("failed to open {}", pathString);
            return false;
        }

        auto* png = png_create_write_struct(PNG_LIBPNG_VER_STRING, nullptr, nullptr, nullptr);
        if (!png) {
            std::fclose(file);
            error = "failed to create png writer";
            return false;
        }

        auto* info = png_create_info_struct(png);
        if (!info) {
            png_destroy_write_struct(&png, nullptr);
            std::fclose(file);
            error = "failed to create png info";
            return false;
        }

        if (setjmp(png_jmpbuf(png))) {
            png_destroy_write_struct(&png, &info);
            std::fclose(file);
            error = std::format("failed to write {}", pathString);
            return false;
        }

        png_init_io(png, file);
        png_set_IHDR(png, info, sc<png_uint_32>(width), sc<png_uint_32>(height), 8, PNG_COLOR_TYPE_RGBA, PNG_INTERLACE_NONE, PNG_COMPRESSION_TYPE_DEFAULT,
                     PNG_FILTER_TYPE_DEFAULT);
        png_set_filter(png, 0, PNG_FILTER_NONE);
        png_write_info(png, info);

        std::vector<png_bytep> rows(sc<size_t>(height));
        for (int y = 0; y < height; ++y) {
            rows[sc<size_t>(y)] = const_cast<png_bytep>(rgba.data() + sc<size_t>(height - 1 - y) * width * 4);
        }

        png_write_image(png, rows.data());
        png_write_end(png, nullptr);
        png_destroy_write_struct(&png, &info);
        std::fclose(file);

        return true;
    }

    bool renderWindowToPNG(PHLWINDOW window, const std::filesystem::path& outputPath, std::string& error) {
        if (!window || !window->m_isMapped) {
            error = "window is not mapped";
            return false;
        }

        const auto monitor = window->m_monitor.lock();
        if (!monitor || !g_pCompositor->monitorExists(monitor)) {
            error = "window monitor is not available";
            return false;
        }

        const auto logicalSize = window->m_realSize->value();
        const int  width       = std::max(1, sc<int>(std::round(logicalSize.x * monitor->m_scale)));
        const int  height      = std::max(1, sc<int>(std::round(logicalSize.y * monitor->m_scale)));

        auto fb = g_pHyprRenderer->createFB("hyprmasq screenshot");
        if (!fb || !fb->alloc(width, height, DRM_FORMAT_ABGR8888)) {
            error = "failed to allocate framebuffer";
            return false;
        }

        auto* glFb = dynamic_cast<Render::GL::CGLFramebuffer*>(fb.get());
        if (!glFb) {
            error = "hyprmasq only supports the GL renderer for V1";
            return false;
        }

        auto oldRenderData = g_pHyprRenderer->m_renderData;
        auto oldRenderMode = g_pHyprRenderer->m_renderMode;
        auto restore       = Hyprutils::Utils::CScopeGuard([&] {
            g_pHyprRenderer->m_bBlockSurfaceFeedback = false;
            g_pHyprRenderer->m_renderMode            = oldRenderMode;
            g_pHyprRenderer->m_renderData            = oldRenderData;
            if (oldRenderData.pMonitor)
                g_pHyprRenderer->setViewport(0, 0, oldRenderData.pMonitor->m_pixelSize.x, oldRenderData.pMonitor->m_pixelSize.y);
        });

        CRegion damage = {0.0, 0.0, sc<double>(width), sc<double>(height)};
        if (!g_pHyprRenderer->beginFullFakeRender(monitor, damage, fb)) {
            error = "failed to begin fake render";
            return false;
        }

        g_pHyprRenderer->m_renderData.fbSize          = Vector2D{width, height};
        g_pHyprRenderer->m_renderData.transformDamage = false;
        g_pHyprRenderer->setProjectionType(Render::RPT_EXPORT);
        g_pHyprRenderer->setViewport(0, 0, width, height);

        Render::GL::g_pHyprOpenGL->makeEGLCurrent();
        glBindFramebuffer(GL_FRAMEBUFFER, glFb->getFBID());
        glViewport(0, 0, width, height);
        glClearColor(0.F, 0.F, 0.F, 0.F);
        glClear(GL_COLOR_BUFFER_BIT);

        g_pHyprRenderer->m_bBlockSurfaceFeedback = g_pHyprRenderer->shouldRenderWindow(window);
        g_pHyprRenderer->renderWindow(window, monitor, Time::steadyNow(), false, Render::RENDER_PASS_ALL, true, true);
        g_pHyprRenderer->m_bBlockSurfaceFeedback = false;
        g_pHyprRenderer->m_renderData.blockScreenShader = true;
        g_pHyprRenderer->endRender();

        std::vector<uint8_t> rgba(sc<size_t>(width) * height * 4);
        Render::GL::g_pHyprOpenGL->makeEGLCurrent();
        glBindFramebuffer(GL_READ_FRAMEBUFFER, glFb->getFBID());
        glPixelStorei(GL_PACK_ALIGNMENT, 1);
        glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
        glPixelStorei(GL_PACK_ALIGNMENT, 4);
        glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);

        return writePNG(outputPath, width, height, rgba, error);
    }

    std::string hyprmasqRequest(eHyprCtlOutputFormat format, std::string request) {
        std::string error;
        const auto  parsed = parseRequest(request, error);
        if (!parsed)
            return error;

        const auto window = windowFromAddress(parsed->windowAddress);
        if (!window)
            return std::format("window not found: {}", parsed->windowAddress);

        if (!renderWindowToPNG(window, parsed->outputPath, error))
            return std::format("screenshot failed: {}", error);

        if (format == FORMAT_JSON)
            return std::format(R"#({{"ok":true,"path":"{}"}})#", parsed->outputPath);

        return "ok";
    }
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    PHANDLE = handle;

    HyprlandAPI::registerHyprCtlCommand(PHANDLE, SHyprCtlCommand{.name = "hyprmasq", .exact = false, .fn = hyprmasqRequest});

    return {"hyprmasq", "window screenshot extraction for Hyprland", "daniel", "0.1.0"};
}

APICALL EXPORT void PLUGIN_EXIT() {
    PHANDLE = nullptr;
}
