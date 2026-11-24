# computer-use-linux, for running the benchmark tasks with it instead of hyprmasq (bench/default.nix with
# backend = "computer-use-linux"): the release's crate from crates.io, unchanged, built as `cargo install
# computer-use-linux` builds it, with its three binaries in one directory, and its agent skill as its Claude Code and
# Codex plugin ships it, from the release's tag, as the crate no longer holds the plugin.
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
  # The newest release, 0.7.59 (tag v0.7.59, commit 34bee24). The crate's SHA-256 on crates.io is
  # ff3a6e912aaecb53f4bcdb90afb16abf2218bddff79230545b21ff632e6c83c3.
  version = "0.7.59";
  tag = "34bee240fcadea5f50956fa2303f077a44364941";

  src = pkgs.fetchCrate {
    inherit pname version;
    hash = "sha256-DTgRHeCwzb0ae1Q9A82y0div6uALKO9qg5Lg2fZ6c60=";
  };

  # The release's tag, for the plugin's skill, which the crate does not hold.
  repository = pkgs.fetchFromGitHub {
    owner = "agent-sh";
    repo = pname;
    rev = tag;
    hash = "sha256-o7wSofaNOf8XOEcvlN3q3lelVZ7ggHMqmnj0KmsBWEk=";
  };

  # `cargo install`: the release profile, the crate's own Cargo.lock (--locked, as its CI builds), all three binaries
  # (the server, its COSMIC helper and its indicator overlay), and no tests.
  server = pkgs.rustPlatform.buildRustPackage {
    inherit pname version src;
    cargoHash = "sha256-oE3YKIcH0dhuA10cp+WFA6rCZiJHi7Vrf8Hg4C8qE50=";
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
  # release's tag (the same as its skills/computer-use-linux), with its references.
  skills = pkgs.runCommand "computer-use-linux-skill-${version}" { } ''
    cp -r ${repository}/plugins/computer-use-linux/skills/computer-use-linux $out
  '';
in
{
  inherit src server skills;
}
