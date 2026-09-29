PLUGIN := hyprmasq.so
TOOL := hyprmasq-input
BUILD_DIR := build

SRC := $(wildcard src/*.cpp)
OBJ := $(SRC:src/%.cpp=$(BUILD_DIR)/%.o)
DEP := $(OBJ:.o=.d)

PLUGIN_PKGS := hyprland libdrm pixman-1 egl glesv2 libpng libeis-1.0 xkbcommon xcb
TOOL_PKGS := libei-1.0

CXX ?= c++
CC ?= cc
CXXFLAGS ?= -O2 -g
CXXFLAGS += -std=c++2b -fPIC -fno-gnu-unique -MMD -MP -Wall -Wextra -Wno-unused-parameter
CFLAGS ?= -O2 -g
CFLAGS += -std=gnu17 -Wall -Wextra
CPPFLAGS += -DWLR_USE_UNSTABLE $(shell pkg-config --cflags $(PLUGIN_PKGS))

.PHONY: all clean

all: $(PLUGIN) $(TOOL)

$(PLUGIN): $(OBJ)
	$(CXX) -shared -fPIC -o $@ $(OBJ) $(shell pkg-config --libs egl glesv2 libpng libeis-1.0 xkbcommon xcb)

$(BUILD_DIR)/%.o: src/%.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

$(TOOL): tools/hyprmasq-input.c
	$(CC) $(CFLAGS) $(shell pkg-config --cflags $(TOOL_PKGS)) -o $@ $< $(shell pkg-config --libs $(TOOL_PKGS))

clean:
	rm -rf $(BUILD_DIR) $(PLUGIN) $(TOOL)

-include $(DEP)
