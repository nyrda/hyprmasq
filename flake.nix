{
  description = "hyprmasq: per-window screenshots and libei input sockets for Hyprland";

  # Pinned to the nixpkgs revision whose Hyprland the plugin is built and VM-tested against.
  # nix/hyprmasq-vm-test.nix reads this pin from flake.lock.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/e554fab72f81915600f3f449b786fd9af40439a5";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in
    {
      # Built against the pinned nixpkgs Hyprland. To build against your own compositor package:
      #   pkgs.callPackage "${inputs.hyprmasq}/nix/package.nix" { }
      packages.${system} = {
        hyprmasq = pkgs.callPackage ./nix/package.nix { };
        default = self.packages.${system}.hyprmasq;
        # The exact Hyprland the plugin above is built and VM-tested against. Running these guarantees a match.
        inherit (pkgs) hyprland xdg-desktop-portal-hyprland;
      };

      # Runs the matching Hyprland, installs hyprmasq-input, and writes /etc/xdg/hypr/hyprmasq_plugin.lua.
      nixosModules.default = import ./nix/module.nix self;

      overlays.default = final: prev: { hyprmasq = final.callPackage ./nix/package.nix { }; };

      checks.${system}.vm-test = import ./nix/hyprmasq-vm-test.nix { inherit system nixpkgs; };

      devShells.${system}.default = pkgs.mkShell { inputsFrom = [ self.packages.${system}.hyprmasq ]; };
    };
}
