{ stdenv, pkg-config, wayland, wayland-scanner, libxkbcommon, hyprland }:
stdenv.mkDerivation {
  pname = "hyprmasq-parent-seat";
  version = "1";
  src = ./parent-seat.c;
  dontUnpack = true;
  nativeBuildInputs = [ pkg-config wayland-scanner ];
  buildInputs = [ wayland libxkbcommon ];
  # The protocols as the pinned Hyprland ships them, which labwc implements in the same versions.
  buildPhase = ''
    cp ${./probe-common.h} probe-common.h
    for protocol in virtual-keyboard-unstable-v1 wlr-virtual-pointer-unstable-v1; do
      wayland-scanner client-header ${hyprland.src}/protocols/$protocol.xml $protocol-client-protocol.h
      wayland-scanner private-code ${hyprland.src}/protocols/$protocol.xml $protocol-protocol.c
    done
    $CC -std=gnu17 -Wall -Wextra -Wno-unused-parameter -I. -x c "$src" -x none virtual-keyboard-unstable-v1-protocol.c wlr-virtual-pointer-unstable-v1-protocol.c \
      $(pkg-config --cflags --libs wayland-client xkbcommon) -o hyprmasq-parent-seat
  '';
  installPhase = ''
    install -Dm755 hyprmasq-parent-seat $out/bin/hyprmasq-parent-seat
  '';
}
