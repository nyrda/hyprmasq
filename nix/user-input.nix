{ stdenv, pkg-config, wayland, wayland-scanner, hyprland }:
stdenv.mkDerivation {
  pname = "hyprmasq-user-input";
  version = "1";
  src = ./user-input.c;
  dontUnpack = true;
  nativeBuildInputs = [ pkg-config wayland-scanner ];
  buildInputs = [ wayland ];
  # The protocols as the pinned Hyprland implements them.
  buildPhase = ''
    for protocol in wlr-virtual-pointer-unstable-v1 input-method-unstable-v2; do
      wayland-scanner client-header ${hyprland.src}/protocols/$protocol.xml $protocol-client-protocol.h
      wayland-scanner private-code ${hyprland.src}/protocols/$protocol.xml $protocol-protocol.c
    done
    $CC -std=gnu17 -Wall -Wextra -Wno-unused-parameter -I. -x c "$src" -x none wlr-virtual-pointer-unstable-v1-protocol.c input-method-unstable-v2-protocol.c \
      $(pkg-config --cflags --libs wayland-client) -o hyprmasq-user-input
  '';
  installPhase = ''
    install -Dm755 hyprmasq-user-input $out/bin/hyprmasq-user-input
  '';
}
