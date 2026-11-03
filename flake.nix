{
  description = "hyprmasq: per-window screenshots and libei input sockets for Hyprland";

  # Hyprland's own flake at the v0.57.0 release commit that hyprland.cachix.org has prebuilt. Everything else, including
  # nixpkgs for the test VM, comes from its package set (nix/pkgs.nix). Do not make it follow another nixpkgs:
  # that changes the derivation, and nothing has it prebuilt.
  inputs.hyprland.url = "github:hyprwm/Hyprland/0aa6745f705f2c4b0a5004eb019ef76e5bbdb426";

  # Only for Go: hyprmasq-mcp talks to Hyprland through its socket, so it does not have to share Hyprland's
  # package set, and mcp/go.mod needs a newer Go than some Hyprland tags pin.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/39ad350a0602fa0a58a544344e3e9187526ea45c";

  outputs =
    {
      self,
      hyprland,
      nixpkgs,
    }:
    let
      system = "x86_64-linux";
      pkgs = import ./nix/pkgs.nix { inherit system hyprland; };
      goPkgs = nixpkgs.legacyPackages.${system};
      vmTest = import ./nix/hyprmasq-vm-test.nix { inherit system pkgs goPkgs; };
    in
    {
      # Built against the pinned Hyprland. To build against your own compositor package:
      #   pkgs.callPackage "${inputs.hyprmasq}/nix/package.nix" { }
      packages.${system} = {
        hyprmasq = vmTest.plugin;
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
        vm-test = vmTest;
        module-compatibility = import ./nix/module-test.nix { inherit self pkgs; };
        # The subtest tables of docs/vm-test.md against the VM test's areas, without a VM.
        vm-test-docs = pkgs.runCommand "hyprmasq-vm-test-docs" { nativeBuildInputs = [ pkgs.python3 ]; } ''
          python3 ${./nix/vm-test}/check.py --docs ${./docs/vm-test.md}
          touch $out
        '';
      }
      // import ./nix/ci-checks.nix {
        inherit pkgs goPkgs nixpkgs;
        mcp = self.packages.${system}.hyprmasq-mcp;
      };

      # Builds the plugin with make, and the MCP server with go in mcp/.
      devShells.${system}.default = pkgs.mkShell.override { inherit (pkgs.hyprland) stdenv; } {
        inputsFrom = [ self.packages.${system}.hyprmasq ];
        packages = [
          goPkgs.go
          goPkgs.clang-tools
          # The Go tests of recordings make videos.
          pkgs.ffmpeg-headless
        ];
      };
    };
}
