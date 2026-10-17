{ stdenv, pkg-config, wayland, wayland-scanner, wayland-protocols }:
stdenv.mkDerivation {
  pname = "hyprmasq-dnd-probe";
  version = "1";
  src = ./dnd-probe.c;
  dontUnpack = true;
  nativeBuildInputs = [ pkg-config wayland-scanner ];
  buildInputs = [ wayland ];
  buildPhase = ''
    wayland-scanner client-header ${wayland-protocols}/share/wayland-protocols/stable/xdg-shell/xdg-shell.xml xdg-shell-client-protocol.h
    wayland-scanner private-code ${wayland-protocols}/share/wayland-protocols/stable/xdg-shell/xdg-shell.xml xdg-shell-protocol.c
    $CC -std=gnu17 -Wall -Wextra -Wno-unused-parameter -I. -x c "$src" -x none xdg-shell-protocol.c \
      $(pkg-config --cflags --libs wayland-client) -o hyprmasq-dnd-probe
  '';
  installPhase = ''
    install -Dm755 hyprmasq-dnd-probe $out/bin/hyprmasq-dnd-probe
  '';
}
