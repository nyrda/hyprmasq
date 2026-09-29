#include "Screenshot.hpp"

#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/helpers/time/Time.hpp>
#include <hyprland/src/output/Monitor.hpp>
#include <hyprland/src/render/OpenGL.hpp>
#include <hyprland/src/render/Renderer.hpp>
#include <hyprland/src/render/gl/GLFramebuffer.hpp>
#include <hyprutils/math/Region.hpp>

#include <GLES3/gl32.h>
#include <png.h>

#include <cmath>
#include <cstdio>
#include <format>
#include <memory>
#include <vector>

using namespace Hyprutils::Math;

namespace hyprmasq {
    namespace {
        // IHyprRenderer::renderWindow is protected. A pointer to it formed in a derived class is
        // ordinary C++, and avoids redefining access specifiers around the include.
        struct SRendererAccess : Render::IHyprRenderer {
            static constexpr auto RENDER_WINDOW = &SRendererAccess::renderWindow;
        };

        struct SFileCloser {
            void operator()(std::FILE* file) const {
                std::fclose(file);
            }
        };

        std::expected<void, std::string> encode(const std::filesystem::path& path, int width, int height, std::vector<uint8_t>& rgba) {
            std::unique_ptr<std::FILE, SFileCloser> file{std::fopen(path.c_str(), "wb")};
            if (!file)
                return std::unexpected(std::format("cannot open {}", path.string()));

            png_structp png  = png_create_write_struct(PNG_LIBPNG_VER_STRING, nullptr, nullptr, nullptr);
            png_infop   info = png ? png_create_info_struct(png) : nullptr;
            if (!png || !info) {
                png_destroy_write_struct(&png, &info);
                return std::unexpected("cannot create png writer");
            }

            if (setjmp(png_jmpbuf(png))) {
                png_destroy_write_struct(&png, &info);
                return std::unexpected(std::format("cannot write {}", path.string()));
            }

            png_init_io(png, file.get());
            // hyprctl answers once the file is written. libpng's defaults, zlib level 6 and every row filter tried, take
            // up to a second for a large window; level 1 with the Sub filter is 4 to 7 times faster, for files about a
            // fifth larger.
            png_set_compression_level(png, 1);
            png_set_filter(png, PNG_FILTER_TYPE_BASE, PNG_FILTER_SUB);
            png_set_IHDR(png, info, width, height, 8, PNG_COLOR_TYPE_RGBA, PNG_INTERLACE_NONE, PNG_COMPRESSION_TYPE_DEFAULT, PNG_FILTER_TYPE_DEFAULT);
            png_write_info(png, info);

            // RPT_EXPORT already renders top-down, so rows are stored in image order.
            std::vector<png_bytep> rows(height);
            for (int y = 0; y < height; ++y)
                rows[y] = rgba.data() + sc<size_t>(y) * width * 4;

            png_write_image(png, rows.data());
            png_write_end(png, nullptr);
            png_destroy_write_struct(&png, &info);
            // The last buffered bytes reach the file only here, so a full disk can show up only here.
            if (std::fclose(file.release()) != 0)
                return std::unexpected(std::format("cannot write {}", path.string()));
            return {};
        }

        // The renderer produces premultiplied alpha, PNG stores straight alpha.
        void unpremultiply(std::vector<uint8_t>& rgba) {
            for (size_t i = 0; i < rgba.size(); i += 4) {
                const uint8_t a = rgba[i + 3];
                if (a == 0 || a == 255)
                    continue;
                for (size_t c = 0; c < 3; ++c)
                    rgba[i + c] = sc<uint8_t>(std::min(255, (rgba[i + c] * 255 + a / 2) / a));
            }
        }
    }

    std::expected<SCapture, std::string> captureWindow(const PHLWINDOW& window, std::optional<SRegion> region) {
        if (!window || !window->m_isMapped)
            return std::unexpected("window is not mapped");

        const auto monitor = window->m_monitor.lock();
        if (!monitor)
            return std::unexpected("window has no monitor");

        const auto logical = window->size(Desktop::View::IGeometric::GEOMETRIC_CURRENT);
        const int  width   = std::max(1, sc<int>(std::round(logical.x * monitor->m_scale)));
        const int  height  = std::max(1, sc<int>(std::round(logical.y * monitor->m_scale)));

        // The region is clamped to the window and read back at the monitor's scale, widened to whole device pixels so that
        // it covers the requested area at fractional scales. The epsilon keeps 10 * 1.2 from rounding up to 13.
        const int logicalWidth = std::max(1, sc<int>(std::round(logical.x))), logicalHeight = std::max(1, sc<int>(std::round(logical.y)));
        SRegion   area = region.value_or(SRegion{0, 0, logicalWidth, logicalHeight});
        if (area.x < 0 || area.y < 0 || area.width < 1 || area.height < 1)
            return std::unexpected("the region needs a non-negative position and a positive size");
        if (area.x >= logicalWidth || area.y >= logicalHeight)
            return std::unexpected(std::format("the region starts outside the {}x{} window", logicalWidth, logicalHeight));
        area.width         = std::min(area.width, logicalWidth - area.x);
        area.height        = std::min(area.height, logicalHeight - area.y);
        const double scale = monitor->m_scale, epsilon = 1e-6;
        const int    readX = std::clamp(sc<int>(std::floor(area.x * scale + epsilon)), 0, width - 1);
        const int    readY = std::clamp(sc<int>(std::floor(area.y * scale + epsilon)), 0, height - 1);
        const int    readW = std::clamp(sc<int>(std::ceil((area.x + area.width) * scale - epsilon)) - readX, 1, width - readX);
        const int    readH = std::clamp(sc<int>(std::ceil((area.y + area.height) * scale - epsilon)) - readY, 1, height - readY);

        auto         fb = g_pHyprRenderer->createFB("hyprmasq screenshot");
        if (!fb || !fb->alloc(width, height, DRM_FORMAT_ABGR8888))
            return std::unexpected("cannot allocate framebuffer");

        auto* glFb = dynamic_cast<Render::GL::CGLFramebuffer*>(fb.get());
        if (!glFb)
            return std::unexpected("only the GL renderer is supported");

        CRegion damage = {0.0, 0.0, sc<double>(width), sc<double>(height)};
        if (!g_pHyprRenderer->beginFullFakeRender(monitor, damage, fb))
            return std::unexpected("cannot begin offscreen render");

        // Mirrors Screenshare::CScreenshareFrame::renderWindow.
        g_pHyprRenderer->m_renderData.fbSize          = Vector2D{width, height};
        g_pHyprRenderer->m_renderData.transformDamage = false;
        g_pHyprRenderer->setProjectionType(Render::RPT_EXPORT);
        g_pHyprRenderer->setViewport(0, 0, width, height);

        glClearColor(0.F, 0.F, 0.F, 0.F);
        glClear(GL_COLOR_BUFFER_BIT);

        // Visible windows already get frame callbacks from the normal render loop.
        g_pHyprRenderer->m_bBlockSurfaceFeedback = g_pHyprRenderer->shouldRenderWindow(window);
        (g_pHyprRenderer.get()->*SRendererAccess::RENDER_WINDOW)(window, monitor, Time::steadyNow(), false, Render::RENDER_PASS_ALL, true, true);
        g_pHyprRenderer->m_bBlockSurfaceFeedback = false;

        g_pHyprRenderer->m_renderData.blockScreenShader = true;
        g_pHyprRenderer->endRender();

        SCapture capture{.width  = readW,
                         .height = readH,
                         .rgba   = std::vector<uint8_t>(sc<size_t>(readW) * readH * 4),
                         .scale  = scale,
                         .region = {readX / scale, readY / scale, readW / scale, readH / scale}};
        Render::GL::g_pHyprOpenGL->makeEGLCurrent();
        glBindFramebuffer(GL_READ_FRAMEBUFFER, glFb->getFBID());
        glPixelStorei(GL_PACK_ALIGNMENT, 1);
        // RPT_EXPORT renders top-down, so framebuffer rows count from the top of the window too.
        glReadPixels(readX, readY, readW, readH, GL_RGBA, GL_UNSIGNED_BYTE, capture.rgba.data());
        glPixelStorei(GL_PACK_ALIGNMENT, 4);
        glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);

        g_pHyprRenderer->m_renderData.pMonitor.reset();
        return capture;
    }

    std::expected<void, std::string> writePNG(SCapture& capture, const std::filesystem::path& output) {
        unpremultiply(capture.rgba);
        return encode(output, capture.width, capture.height, capture.rgba);
    }
}
