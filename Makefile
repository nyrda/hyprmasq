PLUGIN := hyprmasq.so
TOOL := hyprmasq-input
REAPER := hyprmasq-reaper
LAUNCHER := hyprmasq-launch
BUILD_DIR := build
VERSION := $(strip $(shell cat VERSION))
VERSION_CPPFLAGS := -DHYPRMASQ_VERSION=\"$(VERSION)\"
PREFIX ?= /usr/local
DESTDIR ?=

SRC := $(wildcard src/*.cpp)
OBJ := $(SRC:src/%.cpp=$(BUILD_DIR)/%.o)
DEP := $(OBJ:.o=.d)

PLUGIN_PKGS := hyprland libdrm pixman-1 egl glesv2 libpng libeis-1.0 xkbcommon xcb
TOOL_PKGS := libei-1.0 xkbcommon

CXXFLAGS ?= -O2 -g
CXXFLAGS += -std=c++2b -fPIC -fno-gnu-unique -MMD -MP -Wall -Wextra -Wno-unused-parameter
CFLAGS ?= -O2 -g
CFLAGS += -std=gnu17 -Wall -Wextra
CPPFLAGS += $(VERSION_CPPFLAGS) -DWLR_USE_UNSTABLE $(shell pkg-config --cflags $(PLUGIN_PKGS))

.PHONY: all install clean check-hyprland FORCE

# The plugin targets the Hyprland releases that HYPRLAND_VERSION names, one per line: the one flake.lock pins and the
# one before it. Headers of another version can compile and still misbehave, so the build stops unless
# IGNORE_HYPRLAND_VERSION=1 (for porting). Hyprland main reports the version of the last release, so this cannot tell
# main from that release.
HYPRLAND_TARGETS := $(shell cat HYPRLAND_VERSION)
HYPRLAND_FOUND := $(shell pkg-config --modversion hyprland 2>/dev/null)
# The Hyprland that the objects were compiled against, rewritten only when pkg-config finds another, as in another dev
# shell: then the plugin builds anew instead of keeping objects of the other release.
HYPRLAND_STAMP := $(BUILD_DIR)/hyprland
HYPRLAND_BUILT := $(HYPRLAND_FOUND) $(shell pkg-config --variable=prefix hyprland 2>/dev/null)

all: $(PLUGIN) $(TOOL) $(REAPER) $(LAUNCHER)

check-hyprland:
	@pkg-config --print-errors --exists $(PLUGIN_PKGS) || \
		{ echo "error: pkg-config cannot resolve the plugin's build dependencies (see above)." >&2; exit 1; }
ifneq ($(IGNORE_HYPRLAND_VERSION),1)
	@if [ -z "$(filter $(HYPRLAND_FOUND),$(HYPRLAND_TARGETS))" ]; then \
		echo "error: hyprmasq targets Hyprland $(HYPRLAND_TARGETS), pkg-config finds '$(HYPRLAND_FOUND)'." >&2; \
		echo "Build a hyprmasq commit that targets Hyprland $(HYPRLAND_FOUND), or set IGNORE_HYPRLAND_VERSION=1 to port." >&2; \
		exit 1; \
	fi
endif

$(PLUGIN): $(OBJ)
	$(CXX) $(CXXFLAGS) $(LDFLAGS) -shared -fPIC -o $@ $(OBJ) $(shell pkg-config --libs egl glesv2 libpng libeis-1.0 xkbcommon xcb)

$(HYPRLAND_STAMP): FORCE
	@mkdir -p $(dir $@)
	@echo '$(HYPRLAND_BUILT)' | cmp -s - $@ || echo '$(HYPRLAND_BUILT)' > $@

$(BUILD_DIR)/%.o: src/%.cpp VERSION $(HYPRLAND_STAMP) | check-hyprland
	@mkdir -p $(dir $@)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

$(TOOL): tools/hyprmasq-input.c VERSION
	$(CC) $(VERSION_CPPFLAGS) $(CFLAGS) $(LDFLAGS) $(shell pkg-config --cflags $(TOOL_PKGS)) -o $@ $< $(shell pkg-config --libs $(TOOL_PKGS)) -lm

$(REAPER): tools/hyprmasq-reaper.c
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $<

$(LAUNCHER): tools/hyprmasq-launch VERSION
	sed 's/@HYPRMASQ_VERSION@/$(VERSION)/g' $< > $@
	chmod +x $@

install: all
	install -Dm755 $(PLUGIN) "$(DESTDIR)$(PREFIX)/lib/$(PLUGIN)"
	install -Dm755 $(TOOL) "$(DESTDIR)$(PREFIX)/bin/$(TOOL)"
	install -Dm755 $(LAUNCHER) "$(DESTDIR)$(PREFIX)/bin/$(LAUNCHER)"
	install -Dm755 $(REAPER) "$(DESTDIR)$(PREFIX)/libexec/$(REAPER)"

clean:
	rm -rf $(BUILD_DIR) $(PLUGIN) $(TOOL) $(REAPER) $(LAUNCHER)

-include $(DEP)
