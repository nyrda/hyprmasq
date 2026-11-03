# The checks that CI builds besides the packages and vm-test-docs. Each one is a derivation, so a run whose cache has
# its output from an earlier run skips it: a change to the docs alone, for example, runs no Go tests.
{
  pkgs,
  goPkgs,
  nixpkgs,
  mcp,
}:

let
  inherit (pkgs) lib;
  fs = lib.fileset;
  hasAnyExt = exts: file: lib.any file.hasExt exts;
in
{
  # clang-format on the C and C++ sources and gofmt on the MCP server, with the dev shell's versions.
  format =
    let
      src = fs.toSource {
        root = ../.;
        fileset = fs.unions [
          ../.clang-format
          (fs.fileFilter (hasAnyExt [
            "c"
            "h"
            "cpp"
            "hpp"
          ]) ../src)
          (fs.fileFilter (hasAnyExt [
            "c"
            "h"
          ]) ../tools)
          (fs.fileFilter (file: file.hasExt "c") ../nix)
          (fs.fileFilter (file: file.hasExt "go") ../mcp)
        ];
      };
    in
    pkgs.runCommand "hyprmasq-format"
      {
        nativeBuildInputs = [
          goPkgs.clang-tools
          goPkgs.go
        ];
      }
      ''
        cd ${src}
        find src tools nix -type f -name '*.[ch]' -o -type f -name '*.[ch]pp' | sort | xargs clang-format --dry-run -Werror
        files=$(gofmt -l mcp)
        if [[ -n "$files" ]]; then
          printf 'not gofmt-formatted: %s\n' $files
          exit 1
        fi
        touch $out
      '';

  # All Go tests of the MCP server: the package build runs only those that do not wait on real time.
  mcp-tests = mcp.overrideAttrs { checkFlags = [ ]; };

  # The Calc task's checker on sheets that LibreOffice fills.
  bench-calc = import ../bench/tasks/calc/check-test.nix { inherit nixpkgs; };
}
