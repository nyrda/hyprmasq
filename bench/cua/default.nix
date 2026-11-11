# Cua Driver, its Hyprland plugin and its agent skill, for running the benchmark tasks with Cua instead of hyprmasq
# (bench/default.nix with backend = "cua"). All three come from the source of one Cua release, unchanged: the driver
# built with Cua's own Nix package (nix/cua-driver/package.nix of that release), the plugin with CMake as its README
# builds it, and the skill as `cua-driver skills install` installs it on Linux.
#
# `pkgs` is the package set whose Hyprland the plugin is built against and loaded into, with Hyprland's compiler, as
# bench/default.nix builds hyprmasq; by default the nixpkgs of this checkout's flake.lock, for the skill alone.
# `input` selects the plugin's input build (-DCUA_HYPRLAND_INPUT=ON), which its release build kit and Omarchy's
# package build; without it the plugin is the discovery-only default build. Its CMake compile-checks the Hyprland
# internals the input build reads and refuses headers it does not know, instead of pinning one Hyprland as 0.38.0 did.
{
  pkgs ?
    let
      lock = builtins.fromJSON (builtins.readFile ../../flake.lock);
      locked = lock.nodes.${lock.nodes.root.inputs.nixpkgs}.locked;
    in
    import (builtins.fetchTarball {
      url = "https://github.com/NixOS/nixpkgs/archive/${locked.rev}.tar.gz";
      sha256 = locked.narHash;
    }) { },
  input ? true,
}:
let
  inherit (pkgs) lib;
  version = "0.42.1";

  # The commit of the release tag cua-driver-rs-v0.42.1, and only the parts of the monorepo the builds read: the
  # driver's workspace reaches libs/cua's telemetry crate and the files it embeds.
  src = pkgs.fetchgit {
    url = "https://github.com/trycua/cua.git";
    rev = "badaa6e0d59fb5add81474bc04efadb89f713eb9";
    sparseCheckout = [
      "libs/cua-driver/rust"
      "libs/cua-driver/wayland-helper"
      "libs/cua-driver/hyprland-plugin"
      "libs/cua/crates/cua-telemetry"
      "libs/cua/crates/cua-teleport/src/ux"
      "libs/images"
      "nix/cua-driver"
    ];
    hash = "sha256-Hz8HJrOz1wzeskUzQb0OWoRd0Jasl1ClIy0t06HHdPA=";
  };

  # Cua's package builds with the features portal-input and portal-capture. Its Linux release binary, the one its
  # installer puts in place, has portal-input only (cd-rust-cua-driver.yml): the same here.
  driver =
    (import "${src}/nix/cua-driver/package.nix" {
      inherit pkgs;
      src = "${src}/libs";
      sourceSubdir = "cua-driver/rust";
    }).overrideAttrs
      (old: {
        cargoBuildFlags = [
          "-p"
          "cua-driver"
          "--features"
          "cua-driver/portal-input"
        ];
      });

  hyprlandVersion = builtins.head (lib.splitString "+" pkgs.hyprland.version);
  # The only Hyprland on which this release's driver sees a window. Its plugin builds and loads on 0.57.1 too, but on
  # 0.57 `j/monitors` and `j/clients` leave out the id of a named or absent workspace, which the driver's Workspace
  # requires (wayland/hyprland.rs), so that list_windows finds none. Said here, before a series runs and fails every run.
  inputHyprland = "0.56.2";
  hyprland =
    if !input || hyprlandVersion == inputHyprland then
      pkgs.hyprland
    else
      throw (
        "Cua ${version}'s driver sees no window of Hyprland ${hyprlandVersion}, only of ${inputHyprland}: run the Cua "
        + "backend with --source at a checkout of the hyprland-0.56 branch"
      );

  # Same compiler as Hyprland, so the plugin shares its C++ runtime, as its README requires.
  plugin = hyprland.stdenv.mkDerivation {
    pname = "cua-hyprland-plugin${lib.optionalString (!input) "-discovery"}";
    inherit version;
    src = "${src}/libs/cua-driver/hyprland-plugin";

    nativeBuildInputs = [
      pkgs.cmake
      pkgs.ninja
      pkgs.pkg-config
      pkgs.python3
    ];
    buildInputs = [ hyprland ] ++ hyprland.buildInputs;

    cmakeFlags = [
      (lib.cmakeFeature "CMAKE_BUILD_TYPE" "Release")
      (lib.cmakeFeature "CUA_HYPRLAND_EXPECTED_VERSION" hyprlandVersion)
      (lib.cmakeBool "CUA_HYPRLAND_INPUT" input)
      (lib.cmakeBool "BUILD_TESTING" true)
    ];

    # Its CTest suite, as the release recipe runs it.
    doCheck = true;
    checkPhase = ''
      runHook preCheck
      ctest --output-on-failure --no-tests=error
      runHook postCheck
    '';

    meta = {
      description = "Cua's Hyprland plugin, built for Hyprland ${hyprlandVersion}";
      homepage = "https://github.com/trycua/cua";
      license = lib.licenses.mit;
      platforms = lib.platforms.linux;
    };
  };

  # The skill pack as `cua-driver skills install` installs it on Linux: the release's skills archive, which holds
  # the release's rust/Skills/cua-driver, without the guides of the other platforms (skills.rs,
  # excluded_platform_docs).
  skills = pkgs.runCommand "cua-driver-skill-${version}" { } ''
    cp -r ${src}/libs/cua-driver/rust/Skills/cua-driver $out
    chmod -R u+w $out
    rm $out/WINDOWS.md $out/MACOS.md
  '';
in
{
  inherit
    src
    driver
    plugin
    skills
    ;
}
