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
  # The newest release, 0.7.47 (tag v0.7.47, commit 2f1e32d). The crate's SHA-256 on crates.io is
  # 789c213a9aaa4f5996ee87af2826ba4174849bd9cc575a4ab06e8085955bd2ad.
  version = "0.7.47";
  tag = "2f1e32d249b75eae6d241c8b449bd9577bd131f5";

  src = pkgs.fetchCrate {
    inherit pname version;
    hash = "sha256-38ZLfzEYjqx3DvKpCcB2BVFHKs9b8QpAZzguMsTX4qY=";
  };

  # The release's tag, for the plugin's skill, which the crate does not hold.
  repository = pkgs.fetchFromGitHub {
    owner = "agent-sh";
    repo = pname;
    rev = tag;
    hash = "sha256-kB/VgKXOgC3wqbRtsbhW7RpKMvjaFoeSEmq0DeQaibo=";
  };

  # `cargo install`: the release profile, the crate's own Cargo.lock (--locked, as its CI builds), all three binaries
  # (the server, its COSMIC helper and its indicator overlay), and no tests.
  server = pkgs.rustPlatform.buildRustPackage {
    inherit pname version src;
    cargoHash = "sha256-sJqy0Jwf8ewnFBDMBP6fMnZMieDRCojgFqfnxgxHT4M=";
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
