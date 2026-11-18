{
  description = "hyprmasq: per-window screenshots and libei input sockets for Hyprland";

  # Hyprland's own flake at the v0.57.2 release commit that hyprland.cachix.org has prebuilt. Everything else, including
  # nixpkgs for the test VM, comes from its package set (nix/pkgs.nix). Do not make it follow another nixpkgs:
  # that changes the derivation, and nothing has it prebuilt.
  inputs.hyprland.url = "github:hyprwm/Hyprland/11cbc6c577b84ffb1edf4df3fc5b6128c3ae6947";

  # Hyprland's own flake at a commit of its main branch, the next release, which the plugin also builds against and
  # which hyprland.cachix.org has prebuilt, see "Hyprland main" in docs/development.md.
  inputs.hyprland-main.url = "github:hyprwm/Hyprland/5177d94ed31742849d9654da1d9d50e5ad05e26d";

  # Only for Go: hyprmasq-mcp talks to Hyprland through its socket, so it does not have to share Hyprland's
  # package set, and mcp/go.mod needs a newer Go than some Hyprland tags pin.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/39ad350a0602fa0a58a544344e3e9187526ea45c";

  outputs =
    {
      self,
      hyprland,
      hyprland-main,
      nixpkgs,
    }:
    let
      system = "x86_64-linux";
      pkgs = import ./nix/pkgs.nix { inherit system hyprland; };
      goPkgs = nixpkgs.legacyPackages.${system};
      vmTest = import ./nix/hyprmasq-vm-test.nix { inherit system pkgs goPkgs; };
      # The plugin and its test on Hyprland 0.56.2, the older release of HYPRLAND_VERSION, which is the flake's nixpkgs'
      # own Hyprland.
      vmTest056 = import ./nix/hyprmasq-vm-test.nix {
        inherit system goPkgs;
        pkgs = goPkgs;
      };
      # The plugin and its test on Hyprland main, from Hyprland's package set at that commit.
      pkgsMain = import ./nix/pkgs.nix {
        inherit system;
        hyprland = hyprland-main;
      };
      vmTestMain = import ./nix/hyprmasq-vm-test.nix {
        inherit system goPkgs;
        pkgs = pkgsMain;
        hyprlandMain = true;
      };
    in
    {
      # Built against the pinned Hyprland. To build against your own compositor package:
      #   pkgs.callPackage "${inputs.hyprmasq}/nix/package.nix" { }
      packages.${system} = {
        hyprmasq = vmTest.plugin;
        # Also installed as $out/bin/hyprmasq-mcp by the hyprmasq package.
        hyprmasq-mcp = self.packages.${system}.hyprmasq.mcp;
        default = self.packages.${system}.hyprmasq;
        # The plugin for Hyprland 0.56.2, and that Hyprland. The MCP server is the same for both.
        hyprmasq-hyprland056 = vmTest056.plugin;
        hyprland-hyprland056 = goPkgs.hyprland;
        # The plugin for Hyprland main, and that Hyprland.
        hyprmasq-hyprland-main = vmTestMain.plugin;
        hyprland-hyprland-main = pkgsMain.hyprland;
        # The exact Hyprland the plugin above is built and VM-tested against. Running these guarantees a match.
        inherit (pkgs) hyprland xdg-desktop-portal-hyprland;
      };

      # Runs the matching Hyprland, installs hyprmasq-input, and writes /etc/xdg/hypr/hyprmasq_plugin.lua.
      nixosModules.default = import ./nix/module.nix self;

      # The overlay builds against final.hyprland from the consuming package set.
      overlays.default = final: prev: { hyprmasq = final.callPackage ./nix/package.nix { }; };

      checks.${system} = {
        vm-test = vmTest;
        # The same test in containers at once, which needs a GPU and the Nix settings of docs/vm-test.md.
        container-test = import ./nix/container-test.nix { inherit system pkgs goPkgs; };
        # Both on Hyprland 0.56.2, see "Two Hyprland releases" in docs/development.md.
        vm-test-hyprland056 = vmTest056;
        container-test-hyprland056 = import ./nix/container-test.nix {
          inherit system goPkgs;
          pkgs = goPkgs;
        };
        # Both on Hyprland main.
        vm-test-hyprland-main = vmTestMain;
        container-test-hyprland-main = import ./nix/container-test.nix {
          inherit system goPkgs;
          pkgs = pkgsMain;
          hyprlandMain = true;
        };
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
      devShells.${system} = {
        default = pkgs.mkShell.override { inherit (pkgs.hyprland) stdenv; } {
          inputsFrom = [ self.packages.${system}.hyprmasq ];
          packages = [
            goPkgs.go
            goPkgs.clang-tools
            # The Go tests of recordings make videos.
            pkgs.ffmpeg-headless
          ];
        };
        # The same against Hyprland 0.56.2: nix develop .#hyprland056 -c make
        hyprland056 = goPkgs.mkShell.override { inherit (goPkgs.hyprland) stdenv; } {
          inputsFrom = [ self.packages.${system}.hyprmasq-hyprland056 ];
          packages = [
            goPkgs.go
            goPkgs.clang-tools
            goPkgs.ffmpeg-headless
          ];
        };
        # The same against Hyprland main: nix develop .#hyprland-main -c make
        hyprland-main = pkgsMain.mkShell.override { inherit (pkgsMain.hyprland) stdenv; } {
          inputsFrom = [ self.packages.${system}.hyprmasq-hyprland-main ];
          packages = [
            goPkgs.go
            goPkgs.clang-tools
            pkgsMain.ffmpeg-headless
          ];
        };
      };
    };
}
