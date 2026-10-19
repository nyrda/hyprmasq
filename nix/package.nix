# hyprmasq plugin package.
#
# Hyprland plugins must be built against the exact Hyprland they are loaded into, so call this with the
# package set your compositor comes from:
#
#   pkgs.callPackage "${inputs.hyprmasq}/nix/package.nix" { }
#
# Besides the plugin, $out/bin/hyprmasq-input is a small libei client for driving a window's socket,
# $out/bin/hyprmasq-launch starts an application on a hidden workspace, and $out/bin/hyprmasq-mcp is the MCP
# server for coding agents. $out/libexec/hyprmasq-reaper is the plugin's helper that a launch's command runs under.
{
  lib,
  stdenv,
  buildGoModule,
  pkg-config,
  makeWrapper,
  jq,
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
        "mcp"
        "bench"
        # Left by make in the work tree; a copy would keep make from building it.
        "hyprmasq-input"
        "hyprmasq-reaper"
      ])
      && !(lib.hasSuffix ".so" base);
  },
}:

let
  # Built on its own, so changes to the plugin do not rebuild it.
  mcp = import ./mcp-package.nix { inherit lib buildGoModule; };
in
stdenv.mkDerivation {
  pname = "hyprmasq";
  version = lib.trim (lib.fileContents ../VERSION);

  src = hyprmasqSrc;

  nativeBuildInputs = [
    pkg-config
    makeWrapper
  ];

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
    make install PREFIX="$out"
    # hyprctl comes from the user's PATH, so it matches the running Hyprland.
    wrapProgram "$out/bin/hyprmasq-launch" --prefix PATH : ${lib.makeBinPath [ jq ]}
    # The server runs hyprmasq-input from PATH; this one matches the plugin.
    makeWrapper ${lib.getExe mcp} "$out/bin/hyprmasq-mcp" --prefix PATH : "$out/bin"
    runHook postInstall
  '';

  passthru = { inherit mcp hyprland; };

  meta = {
    description = "Hyprland plugin for per-window screenshots and libei input sockets";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
  };
}
