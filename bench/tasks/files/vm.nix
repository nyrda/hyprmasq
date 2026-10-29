# The file manager task's machine: Nautilus, GNOME's file manager and Omarchy's, from nixpkgs. setup.py makes Mara's
# folder Project and starts Nautilus on it. Nautilus needs a session bus, which alice's systemd user manager runs and
# setup.py points it to, and finds dconf on it. It needs nothing else for a local folder: without gvfs it has no
# Network place, and without localsearch no search, which the task does not need.
{ pkgs, ... }:
let
  # The task's files without Python's caches, which a run of the checker on the host may leave.
  task = builtins.path {
    path = ./.;
    name = "hyprmasq-bench-files";
    filter = path: _: baseNameOf path != "__pycache__";
  };
in
{
  environment.systemPackages = [
    pkgs.nautilus
    pkgs.python3
  ];

  environment.etc."hyprmasq-bench/files".source = task;
  # Root's log of Hyprland's events, the form task's.
  environment.etc."hyprmasq-bench/files-windows.py".source = ../form/windows.py;
}
