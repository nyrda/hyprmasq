# A plugin that hooks one function that hyprmasq hooks too, once for each of three functions, for the VM test.
{ stdenv, pkg-config, plugin }:
stdenv.mkDerivation {
  pname = "hyprmasq-hook-taker";
  version = "1";
  src = ./hook-taker.cpp;
  dontUnpack = true;
  nativeBuildInputs = [ pkg-config ];
  # What the plugin builds against, from the same Hyprland.
  inherit (plugin) buildInputs;
  buildPhase = ''
    for kind in seat xwm idle; do
      $CXX -std=c++2b -shared -fPIC -fno-gnu-unique -Wall -Wextra -Wno-unused-parameter -DWLR_USE_UNSTABLE \
        -DHOOK_TAKER_''${kind^^} $(pkg-config --cflags hyprland libdrm pixman-1) "$src" -o hyprmasq-hook-taker-$kind.so
    done
  '';
  installPhase = ''
    for kind in seat xwm idle; do
      install -Dm755 hyprmasq-hook-taker-$kind.so $out/lib/hyprmasq-hook-taker-$kind.so
    done
  '';
}
