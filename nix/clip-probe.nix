{ stdenv, pkg-config, wayland, wayland-scanner, wayland-protocols }:
stdenv.mkDerivation {
  pname = "hyprmasq-clip-probe";
  version = "1";
  src = ./clip-probe.c;
  dontUnpack = true;
  nativeBuildInputs = [ pkg-config wayland-scanner ];
  buildInputs = [ wayland ];
  buildPhase = ''
    cp ${./probe-common.h} probe-common.h
    for xml in stable/xdg-shell/xdg-shell.xml unstable/primary-selection/primary-selection-unstable-v1.xml; do
      name=$(basename $xml .xml)
      wayland-scanner client-header ${wayland-protocols}/share/wayland-protocols/$xml $name-client-protocol.h
      wayland-scanner private-code ${wayland-protocols}/share/wayland-protocols/$xml $name-protocol.c
    done
    $CC -std=gnu17 -Wall -Wextra -Wno-unused-parameter -I. -x c "$src" -x none xdg-shell-protocol.c primary-selection-unstable-v1-protocol.c \
      $(pkg-config --cflags --libs wayland-client) -o hyprmasq-clip-probe
  '';
  installPhase = ''
    install -Dm755 hyprmasq-clip-probe $out/bin/hyprmasq-clip-probe
  '';
}
