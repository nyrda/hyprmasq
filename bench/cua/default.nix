# Cua Driver, its Hyprland plugin and its agent skill, for running the benchmark tasks with Cua instead of hyprmasq
# (bench/default.nix with backend = "cua"). All three come from the source of one Cua release, unchanged: the driver
# built with Cua's own Nix package (nix/cua-driver/package.nix of that release), the plugin with CMake as its README
# builds it, and the skill as `cua-driver skills install` installs it on Linux.
#
# `pkgs` is the package set whose Hyprland the plugin is built against and loaded into, with Hyprland's compiler, as
# bench/default.nix builds hyprmasq; by default the nixpkgs of this checkout's flake.lock, for the skill alone.
# `input` selects the plugin's input build (-DCUA_HYPRLAND_INPUT=ON), which its release build kit and Omarchy's
# package build; without it the plugin is the discovery-only default build. The input build accepts only the
# Hyprland its release pins, 0.56.2 for 0.38.0: CMake refuses any other.
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
  version = "0.38.0";

  # The commit of the release tag cua-driver-rs-v0.38.0, and only the parts of the monorepo the builds read: the
  # driver's workspace reaches libs/cua's telemetry crate and the files it embeds.
  src = pkgs.fetchgit {
    url = "https://github.com/trycua/cua.git";
    rev = "85e2b1318bd77ccf3cfa88ccdf875a4bb7eaed19";
    sparseCheckout = [
      "libs/cua-driver/rust"
      "libs/cua-driver/wayland-helper"
      "libs/cua-driver/hyprland-plugin"
      "libs/cua/crates/cua-telemetry"
      "libs/cua/crates/cua-teleport/src/ux"
      "libs/images"
      "nix/cua-driver"
    ];
    hash = "sha256-Pru8WqK1DbsKDaBhyDgZf6b2tWYneYiXNSSXB7AQ2YI=";
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
  # The only Hyprland the release's input build takes (hyprland-plugin/CMakeLists.txt: "Input is pinned to Hyprland
  # 0.56.2"); its driver does not see Hyprland 0.57.0's windows either. Said here, before a build fails in CMake.
  inputHyprland = "0.56.2";
  hyprland =
    if !input || hyprlandVersion == inputHyprland then
      pkgs.hyprland
    else
      throw (
        "Cua ${version}'s input build needs Hyprland ${inputHyprland}, got ${hyprlandVersion}: run the Cua backend "
        + "with --source at a checkout of the hyprland-0.56 branch"
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
