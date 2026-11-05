# The whole test of nix/vm-test in systemd-nspawn containers at once, instead of one after the other in a VM: one build
# per group of areas, which Nix runs side by side, up to max-jobs. Each group also runs core. See docs/vm-test.md.
{
  system ? "x86_64-linux",
  pkgs ? import ./pkgs.nix { inherit system; },
  ...
}@args:
let
  inherit (pkgs) lib;
  # Of about the same length, from the areas' times in a run. Areas stay with what they require, which the test would
  # otherwise run in their group as well.
  groups = {
    agent = [ "mcp" "mcp-shots" ];
    input = [ "input" "paging" "idle" ];
    scroll = [ "scrolling" "cursor" "typing" ];
    windows = [ "new-windows" "anr" "recording" "qt-popups" ];
    drag = [ "drag" "compositor-log" "xwayland" "hooks" ];
    waits = [ "waits" "suspended" "unload" "shots" "x11-menus" ];
    focus = [ "launch" "shared-focus" "selections" "settle" "key-pace" "leases" ];
  };
  allAreas = map (area: area.name) (builtins.fromJSON (builtins.readFile ./vm-test/areas.json));
  grouped = lib.concatLists (lib.attrValues groups);
  # core runs in every group.
  expected = lib.remove "core" allAreas;
  missing = lib.subtractLists grouped expected;
  twice = lib.unique (lib.filter (name: lib.count (n: n == name) grouped > 1) grouped);
  unknown = lib.subtractLists expected grouped;
in
if missing != [ ] || twice != [ ] || unknown != [ ] then
  throw "the groups of nix/container-test.nix must name each area but core once: missing ${toString missing}, more than once ${toString twice}, unknown ${toString unknown}"
else
  pkgs.linkFarm "hyprmasq-container-test" (
    lib.mapAttrsToList (label: areas: {
      name = label;
      path = import ./hyprmasq-vm-test.nix (
        args
        // {
          inherit
            system
            pkgs
            areas
            label
            ;
          container = true;
        }
      );
    }) groups
  )
