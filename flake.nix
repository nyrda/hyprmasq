{
  description = "hyprmasq: per-window screenshots and libei input sockets for Hyprland";

  # Pinned to the nixpkgs revision whose Hyprland the plugin is built and VM-tested against.
  # nix/hyprmasq-vm-test.nix reads this pin from flake.lock.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/39ad350a0602fa0a58a544344e3e9187526ea45c";

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
        # Also installed as $out/bin/hyprmasq-mcp by the hyprmasq package.
        hyprmasq-mcp = self.packages.${system}.hyprmasq.mcp;
        default = self.packages.${system}.hyprmasq;
        # The exact Hyprland the plugin above is built and VM-tested against. Running these guarantees a match.
        inherit (pkgs) hyprland xdg-desktop-portal-hyprland;
      };

      # Runs the matching Hyprland, installs hyprmasq-input, and writes /etc/xdg/hypr/hyprmasq_plugin.lua.
      nixosModules.default = import ./nix/module.nix self;

      # The overlay builds against final.hyprland from the consuming package set.
      overlays.default = final: prev: { hyprmasq = final.callPackage ./nix/package.nix { }; };

      checks.${system} = {
        vm-test = import ./nix/hyprmasq-vm-test.nix { inherit system nixpkgs; };
        module-compatibility = import ./nix/module-test.nix { inherit self pkgs; };
        # The subtest tables of docs/vm-test.md against the VM test's areas, without a VM.
        vm-test-docs = pkgs.runCommand "hyprmasq-vm-test-docs" { nativeBuildInputs = [ pkgs.python3 ]; } ''
          python3 ${./nix/vm-test}/check.py --docs ${./docs/vm-test.md}
          touch $out
        '';
      };

      # Builds the plugin with make, and the MCP server with go in mcp/.
      devShells.${system}.default = pkgs.mkShell {
        inputsFrom = [ self.packages.${system}.hyprmasq ];
        packages = [
          pkgs.go
          pkgs.clang-tools
          # The Go tests of recordings make videos.
          pkgs.ffmpeg-headless
        ];
      };
    };
}
