# A plugin that calls two functions that hyprmasq hooks as an optimizing build of Hyprland does, for the VM test.
{ stdenv, pkg-config, plugin }:
stdenv.mkDerivation {
  pname = "hyprmasq-call-probe";
  version = "1";
  src = ./call-probe.cpp;
  dontUnpack = true;
  nativeBuildInputs = [ pkg-config ];
  # What the plugin builds against, from the same Hyprland.
  inherit (plugin) buildInputs;
  buildPhase = ''
    $CXX -std=c++2b -shared -fPIC -fno-gnu-unique -Wall -Wextra -Wno-unused-parameter -DWLR_USE_UNSTABLE \
      $(pkg-config --cflags hyprland libdrm pixman-1) "$src" -o hyprmasq-call-probe.so
  '';
  installPhase = ''
    install -Dm755 hyprmasq-call-probe.so $out/lib/hyprmasq-call-probe.so
  '';
}
