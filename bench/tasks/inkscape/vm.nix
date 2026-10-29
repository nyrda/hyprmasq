# The Inkscape task's machine: Inkscape from nixpkgs, 1.4.4 at this pin, the release that the laptop's runs of
# bench/laptop/run_inkscape.py had from Arch, and Mara's drawing.
{ pkgs, ... }:
let
  # The task's files without Python's caches, which a run of the checker on the host may leave.
  task = builtins.path {
    path = ./.;
    name = "hyprmasq-bench-inkscape";
    filter = path: _: baseNameOf path != "__pycache__";
  };
in
{
  environment.systemPackages = [
    pkgs.inkscape
    pkgs.python3
  ];

  environment.etc."hyprmasq-bench/inkscape".source = task;
}
