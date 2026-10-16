# Tests the Calc task's checker on sheets that a headless LibreOffice fills and saves, in the Nix build:
#   nix-build bench/tasks/calc/check-test.nix
# The output is the verdict of each case; the build fails if one differs from what check_test.py expects.
{
  system ? "x86_64-linux",
  nixpkgs ?
    let
      locked = (builtins.fromJSON (builtins.readFile ../../../flake.lock)).nodes.nixpkgs.locked;
    in
    builtins.fetchTarball {
      url = "https://github.com/NixOS/nixpkgs/archive/${locked.rev}.tar.gz";
      sha256 = locked.narHash;
    },
}:
let
  pkgs = import nixpkgs { inherit system; };
  task = builtins.path {
    path = ./.;
    name = "hyprmasq-bench-calc";
    filter = path: _: baseNameOf path != "__pycache__";
  };
  python = pkgs.python3.withPackages (ps: [ ps.lz4 ]);
  lo = pkgs.libreoffice.unwrapped;
in
pkgs.runCommand "hyprmasq-bench-calc-check-test"
  {
    nativeBuildInputs = [ pkgs.libreoffice ];
  }
  ''
    export HOME=$TMPDIR PYTHONDONTWRITEBYTECODE=1
    export URE_BOOTSTRAP=vnd.sun.star.pathname:${lo}/lib/libreoffice/program/fundamentalrc
    export PYTHONPATH=${lo}/lib/libreoffice/program
    ${python}/bin/python3 ${task}/sheet.py "$TMPDIR/sheet.ods"
    ${python}/bin/python3 ${task}/check_test.py "$TMPDIR/sheet.ods" | tee $out
  ''
