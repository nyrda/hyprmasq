{ stdenv, pkg-config, wayland, wayland-scanner, wayland-protocols, libgbm }:
stdenv.mkDerivation {
  pname = "hyprmasq-swapchain-probe";
  version = "1";
  src = ./swapchain-probe.c;
  dontUnpack = true;
  nativeBuildInputs = [ pkg-config wayland-scanner ];
  buildInputs = [ wayland libgbm ];
  buildPhase = ''
    cp ${./probe-common.h} probe-common.h
    for xml in stable/xdg-shell/xdg-shell.xml stable/linux-dmabuf/linux-dmabuf-v1.xml; do
      name=$(basename $xml .xml)
      wayland-scanner client-header ${wayland-protocols}/share/wayland-protocols/$xml $name-client-protocol.h
      wayland-scanner private-code ${wayland-protocols}/share/wayland-protocols/$xml $name-protocol.c
    done
    $CC -std=gnu17 -Wall -Wextra -Wno-unused-parameter -I. -x c "$src" -x none xdg-shell-protocol.c linux-dmabuf-v1-protocol.c \
      $(pkg-config --cflags --libs wayland-client gbm) -o hyprmasq-swapchain-probe
  '';
  installPhase = ''
    install -Dm755 hyprmasq-swapchain-probe $out/bin/hyprmasq-swapchain-probe
  '';
}
