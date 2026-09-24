TARGET := hyprmasq.so
BUILD_DIR := build

SRC := main.cpp
OBJ := $(SRC:%.cpp=$(BUILD_DIR)/%.o)
DEP := $(OBJ:.o=.d)

PKG_CONFIG_CFLAGS := $(shell pkg-config --cflags hyprland libdrm pixman-1 egl glesv2 libpng)
PKG_CONFIG_LIBS := $(shell pkg-config --libs egl glesv2 libpng)

CXX ?= c++
CXXFLAGS ?= -O2 -g
CXXFLAGS += -std=c++2b -fPIC -MMD -MP
CPPFLAGS += -DWLR_USE_UNSTABLE $(PKG_CONFIG_CFLAGS)
LDFLAGS += -shared --no-gnu-unique
LDLIBS += $(PKG_CONFIG_LIBS)

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(OBJ)
	$(CXX) $(LDFLAGS) -o $@ $(OBJ) $(LDLIBS)

$(BUILD_DIR)/%.o: %.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

clean:
	rm -rf $(BUILD_DIR) $(TARGET)

-include $(DEP)
