# A plugin that hooks one function that hyprmasq hooks too, for the VM test: once for each of three functions, and each
# in three ways, see hook-taker.cpp.
{ stdenv, pkg-config, plugin }:
stdenv.mkDerivation {
  pname = "hyprmasq-hook-taker";
  version = "2";
  src = ./hook-taker.cpp;
  dontUnpack = true;
  nativeBuildInputs = [ pkg-config ];
  # What the plugin builds against, from the same Hyprland.
  inherit (plugin) buildInputs;
  # The code of shared hooks, which the plugin has too.
  chainHeader = ../src/HookChain.hpp;
  chainSource = ../src/HookChain.cpp;
  buildPhase = ''
    mkdir chain
    cp "$chainHeader" chain/HookChain.hpp
    cp "$chainSource" chain/HookChain.cpp
    flags="-std=c++2b -shared -fPIC -fno-gnu-unique -Wall -Wextra -Wno-unused-parameter -DWLR_USE_UNSTABLE"
    for kind in seat xwm idle; do
      $CXX $flags -DHOOK_TAKER_''${kind^^} $(pkg-config --cflags hyprland libdrm pixman-1) "$src" -o hyprmasq-hook-taker-$kind.so
      $CXX $flags -DHOOK_TAKER_''${kind^^} -DHOOK_TAKER_OPAQUE $(pkg-config --cflags hyprland libdrm pixman-1) "$src" \
        -o hyprmasq-hook-taker-$kind-opaque.so
      $CXX $flags -DHOOK_TAKER_''${kind^^} -DHOOK_TAKER_CHAIN -Ichain $(pkg-config --cflags hyprland libdrm pixman-1) "$src" chain/HookChain.cpp \
        -o hyprmasq-hook-taker-$kind-chain.so
    done
  '';
  installPhase = ''
    for kind in seat xwm idle; do
      for way in "" -opaque -chain; do
        install -Dm755 hyprmasq-hook-taker-$kind$way.so $out/lib/hyprmasq-hook-taker-$kind$way.so
      done
    done
  '';
}
