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

.PHONY: all install clean

all: $(PLUGIN) $(TOOL) $(REAPER) $(LAUNCHER)

$(PLUGIN): $(OBJ)
	$(CXX) -shared -fPIC -o $@ $(OBJ) $(shell pkg-config --libs egl glesv2 libpng libeis-1.0 xkbcommon xcb)

$(BUILD_DIR)/%.o: src/%.cpp VERSION
	@mkdir -p $(dir $@)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

$(TOOL): tools/hyprmasq-input.c VERSION
	$(CC) $(VERSION_CPPFLAGS) $(CFLAGS) $(shell pkg-config --cflags $(TOOL_PKGS)) -o $@ $< $(shell pkg-config --libs $(TOOL_PKGS)) -lm

$(REAPER): tools/hyprmasq-reaper.c
	$(CC) $(CFLAGS) -o $@ $<

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
