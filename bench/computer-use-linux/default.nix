# computer-use-linux, for running the benchmark tasks with it instead of hyprmasq (bench/default.nix with
# backend = "computer-use-linux"): the release's crate from crates.io, unchanged, built as `cargo install
# computer-use-linux` builds it, with its three binaries in one directory, and its agent skill as its Claude Code and
# Codex plugin ships it.
#
# `pkgs` is the package set of the VM; by default the nixpkgs of this checkout's flake.lock.
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
}:
let
  inherit (pkgs) lib;
  pname = "computer-use-linux";
  # The newest release, 0.7.32 (tag v0.7.32, commit 1f3b8a0). The crate's SHA-256 on crates.io is
  # 6dafe72d37639380340ab0563f57886f5c93bdbf855c5d71bdc177f36225e985.
  version = "0.7.32";

  src = pkgs.fetchCrate {
    inherit pname version;
    hash = "sha256-0IS/0fwO9z+8EnzcNTl/UWpxIxzjsjZUNPRUneqqm14=";
  };

  # `cargo install`: the release profile, the crate's own Cargo.lock (--locked, as its CI builds), all three binaries
  # (the server, its COSMIC helper and its indicator overlay), and no tests.
  server = pkgs.rustPlatform.buildRustPackage {
    inherit pname version src;
    cargoHash = "sha256-x52oG45S01YpocrW5IKoESrEZGxA7mol7chPOOZMlV4=";
    doCheck = false;

    meta = {
      description = "Native Linux desktop control for AI agents over MCP";
      homepage = "https://github.com/agent-sh/computer-use-linux";
      license = lib.licenses.mit;
      mainProgram = "computer-use-linux";
      platforms = lib.platforms.linux;
    };
  };

  # The skill its Claude Code and Codex plugin ships, plugins/computer-use-linux/skills/computer-use-linux of the
  # release (the same as its skills/computer-use-linux).
  skills = pkgs.runCommand "computer-use-linux-skill-${version}" { } ''
    cp -r ${src}/plugins/computer-use-linux/skills/computer-use-linux $out
  '';
in
{
  inherit src server skills;
}
