{ stdenv, pkg-config, wayland, wayland-scanner, wayland-protocols }:
stdenv.mkDerivation {
  pname = "hyprmasq-suspend-probe";
  version = "1";
  src = ./suspend-probe.c;
  dontUnpack = true;
  nativeBuildInputs = [ pkg-config wayland-scanner ];
  buildInputs = [ wayland ];
  buildPhase = ''
    cp ${./probe-common.h} probe-common.h
    xml=${wayland-protocols}/share/wayland-protocols/stable/xdg-shell/xdg-shell.xml
    wayland-scanner client-header $xml xdg-shell-client-protocol.h
    wayland-scanner private-code $xml xdg-shell-protocol.c
    $CC -std=gnu17 -Wall -Wextra -Wno-unused-parameter -I. -x c "$src" -x none xdg-shell-protocol.c \
      $(pkg-config --cflags --libs wayland-client) -o hyprmasq-suspend-probe
  '';
  installPhase = ''
    install -Dm755 hyprmasq-suspend-probe $out/bin/hyprmasq-suspend-probe
  '';
}
