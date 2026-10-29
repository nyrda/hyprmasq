{ stdenv, pkg-config, wayland, wayland-scanner, wayland-protocols, libei }:
stdenv.mkDerivation {
  pname = "hyprmasq-focus-probe";
  version = "1";
  src = ./focus-probe.c;
  dontUnpack = true;
  nativeBuildInputs = [ pkg-config wayland-scanner ];
  buildInputs = [ wayland libei ];
  buildPhase = ''
    cp ${./probe-common.h} probe-common.h
    wayland-scanner client-header ${wayland-protocols}/share/wayland-protocols/stable/xdg-shell/xdg-shell.xml xdg-shell-client-protocol.h
    wayland-scanner private-code ${wayland-protocols}/share/wayland-protocols/stable/xdg-shell/xdg-shell.xml xdg-shell-protocol.c
    foreign=${wayland-protocols}/share/wayland-protocols/unstable/xdg-foreign/xdg-foreign-unstable-v2.xml
    wayland-scanner client-header $foreign xdg-foreign-unstable-v2-client-protocol.h
    wayland-scanner private-code $foreign xdg-foreign-unstable-v2-protocol.c
    activation=${wayland-protocols}/share/wayland-protocols/staging/xdg-activation/xdg-activation-v1.xml
    wayland-scanner client-header $activation xdg-activation-v1-client-protocol.h
    wayland-scanner private-code $activation xdg-activation-v1-protocol.c
    $CC -std=gnu17 -Wall -Wextra -Wno-unused-parameter -I. -x c "$src" -x none xdg-shell-protocol.c xdg-foreign-unstable-v2-protocol.c xdg-activation-v1-protocol.c \
      $(pkg-config --cflags --libs wayland-client libei-1.0) -o hyprmasq-focus-probe
  '';
  installPhase = ''
    install -Dm755 hyprmasq-focus-probe $out/bin/hyprmasq-focus-probe
  '';
}
