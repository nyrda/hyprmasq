# Evaluation checks for the module's Hyprland compatibility assertion.
{ self, pkgs }:
let
  lib = pkgs.lib;
  plugin = self.packages.${pkgs.stdenv.hostPlatform.system}.hyprmasq;
  evaluate = settings:
    (import (pkgs.path + "/nixos/lib/eval-config.nix") {
      system = pkgs.stdenv.hostPlatform.system;
      modules = [
        self.nixosModules.default
        { programs.hyprmasq.enable = true; }
        settings
      ];
    }).config;
  guard = settings:
    lib.findFirst
      (entry: lib.hasPrefix "hyprmasq was built against" entry.message)
      (throw "hyprmasq compatibility assertion is missing")
      (evaluate settings).assertions;
  default = guard { };
  matching = guard {
    programs.hyprmasq.package = plugin;
    programs.hyprland.package = plugin.hyprland;
  };
  mismatched = guard {
    programs.hyprland.package = plugin.hyprland.overrideAttrs {
      version = "0.0.0-test";
      __intentionallyOverridingVersion = true;
    };
  };
  sameVersionHyprland = plugin.hyprland.overrideAttrs (old: {
    postPatch = (old.postPatch or "") + "\n# Test a different build of the same version.\n";
  });
  sameVersion = guard { programs.hyprland.package = sameVersionHyprland; };
  missingPassthru = guard { programs.hyprmasq.package = pkgs.hello; };
in
assert default.assertion;
assert matching.assertion;
assert !mismatched.assertion;
assert lib.hasInfix "0.0.0-test" mismatched.message;
assert sameVersionHyprland.version == plugin.hyprland.version;
assert !sameVersion.assertion;
assert lib.hasInfix (builtins.unsafeDiscardStringContext plugin.hyprland.outPath) sameVersion.message;
assert lib.hasInfix (builtins.unsafeDiscardStringContext sameVersionHyprland.outPath) sameVersion.message;
assert lib.hasInfix "programs.hyprmasq.package = pkgs.callPackage" sameVersion.message;
assert !missingPassthru.assertion;
assert lib.hasInfix "unknown" missingPassthru.message;
pkgs.runCommand "hyprmasq-module-compatibility" { } "touch $out"
