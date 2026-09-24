# hyprmasq plugin package.
#
# Hyprland plugins must be built against the exact Hyprland they are loaded into, so call this with the
# package set your compositor comes from:
#
#   pkgs.callPackage "${inputs.hyprmasq}/nix/package.nix" { }
#
# Besides the plugin, $out/bin/hyprmasq-input is a small libei client for driving a window's socket.
{
  lib,
  stdenv,
  pkg-config,
  hyprland,
  aquamarine,
  cairo,
  glslang,
  hyprcursor,
  hyprgraphics,
  hyprlang,
  hyprutils,
  libGL,
  libdrm,
  libei,
  libinput,
  libpng,
  libxkbcommon,
  lua5_5,
  pixman,
  wayland,
  libxcb,
  libxcb-errors,
  libxcb-wm,
  hyprmasqSrc ? lib.cleanSourceWith {
    src = ../.;
    filter =
      path: type:
      let
        base = baseNameOf path;
      in
      !(lib.hasPrefix ".git" base)
      && !(lib.hasPrefix "result" base)
      && !(lib.elem base [
        "build"
        "nix"
        "docs"
      ])
      && !(lib.hasSuffix ".so" base);
  },
}:

stdenv.mkDerivation {
  pname = "hyprmasq";
  version = "0.2.0-hyprland-${hyprland.version}";

  src = hyprmasqSrc;

  nativeBuildInputs = [ pkg-config ];

  buildInputs = [
    hyprland.dev
    aquamarine
    cairo
    glslang
    hyprcursor
    hyprgraphics
    hyprlang
    hyprutils
    libGL
    libdrm
    libei
    libinput
    libpng
    libxkbcommon
    lua5_5
    pixman
    wayland
    libxcb
    libxcb-errors
    libxcb-wm
  ];

  enableParallelBuilding = true;

  installPhase = ''
    runHook preInstall
    install -Dm755 hyprmasq.so "$out/lib/hyprmasq.so"
    install -Dm755 hyprmasq-input "$out/bin/hyprmasq-input"
    runHook postInstall
  '';

  meta = {
    description = "Hyprland plugin for per-window screenshots and libei input sockets";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
  };
}
