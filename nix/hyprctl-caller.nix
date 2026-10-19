# A plugin that calls hyprctl commands through HyprlandAPI::invokeHyprctlCommand, for the VM test.
{ stdenv, pkg-config, plugin }:
stdenv.mkDerivation {
  pname = "hyprmasq-hyprctl-caller";
  version = "1";
  src = ./hyprctl-caller.cpp;
  dontUnpack = true;
  nativeBuildInputs = [ pkg-config ];
  # What the plugin builds against, from the same Hyprland.
  inherit (plugin) buildInputs;
  buildPhase = ''
    $CXX -std=c++2b -shared -fPIC -fno-gnu-unique -Wall -Wextra -Wno-unused-parameter -DWLR_USE_UNSTABLE \
      $(pkg-config --cflags hyprland libdrm pixman-1) "$src" -o hyprmasq-hyprctl-caller.so
  '';
  installPhase = ''
    install -Dm755 hyprmasq-hyprctl-caller.so $out/lib/hyprmasq-hyprctl-caller.so
  '';
}
