# The form task's machine: the questionnaire in one of several UI toolkits. benchVariant, from default.nix, is the
# number of its questions, 10 to 40, for the GTK 4 app, or TOOLKIT-N for the app of toolkits/TOOLKIT.nix with N
# questions; items.py has them. A variant that ends in -no-dropdowns, such as 25-no-dropdowns, asks no dropdowns:
# the first N, 10 to 30, of the checkboxes, sliders and texts, which setup.py gives the app and check.py through
# $HYPRMASQ_FORM_KINDS.
#
# Every toolkit's app is the command hyprmasq-bench-form STATE-DIR COUNT [STATE-FD], which the launcher below
# writes: it records its process id in STATE-DIR/app.pid and runs the app in that process. The app shows the first
# COUNT questions of items.py in a top-level window titled "Questionnaire", logs every change of an answer to
# STATE-DIR/events.jsonl and every press of Submit to STATE-DIR/submissions.jsonl as form.py does, and on SIGUSR1
# to that process writes its state to the file descriptor STATE-FD, or without it to STATE-DIR/state.json, which
# check.py grades. A toolkit file is a function of { pkgs, lib, task, launcher, xwayland } to
# { app; packages ? [ ]; popups ? [ ]; }, the packages being others the app needs in the VM. They are values only:
# the module's shape cannot depend on pkgs. An X11 app runs on Hyprland's XWayland, which the VM's Hyprland has:
# xwayland runs its arguments with DISPLAY set to it, since setup.py starts the app with Wayland's variables only.
# Its menus and lists are windows of their own, which Hyprland reports as it does other windows: popups are their
# classes, which check.py does not count as windows other than the questionnaire.
{
  pkgs,
  lib,
  benchVariant,
  ...
}:
let
  # The task's files without Python's caches, which a run of the checker on the host may leave.
  task = builtins.path {
    path = ./.;
    name = "hyprmasq-bench-form";
    filter = path: _: baseNameOf path != "__pycache__";
  };
  # $out/bin/hyprmasq-bench-form, which records its process id and execs COMMAND with its arguments.
  launcher = command: ''
    cat > $out/bin/hyprmasq-bench-form <<SCRIPT
    #!${pkgs.runtimeShell}
    echo \$\$ > "\$1/app.pid"
    exec ${command} "\$@"
    SCRIPT
    chmod +x $out/bin/hyprmasq-bench-form
  '';
  # Hyprland starts XWayland on the first free display, the only one in the VM, and listens on its socket from its
  # own start.
  xwayland = pkgs.writeShellScript "hyprmasq-bench-xwayland" ''
    for socket in /tmp/.X11-unix/X*; do
      export DISPLAY=:''${socket#/tmp/.X11-unix/X}
      break
    done
    exec "$@"
  '';
  parts = builtins.match "(([a-z0-9-]+)-)?([0-9]+)(-no-dropdowns)?" benchVariant;
  toolkit =
    if parts == null || builtins.elemAt parts 1 == null then "gtk4" else builtins.elemAt parts 1;
  count = if parts == null then 0 else lib.toIntBase10 (builtins.elemAt parts 2);
  noDropdowns = parts != null && builtins.elemAt parts 3 != null;
  # The kinds of question, as items.py names them: empty for all of them.
  kinds = lib.optionalString noDropdowns "checkbox,slider,text";
  file = ./toolkits + "/${toolkit}.nix";
  chosen =
    if builtins.pathExists file then
      import file {
        inherit
          pkgs
          lib
          task
          launcher
          xwayland
          ;
      }
    else
      { app = pkgs.emptyDirectory; };
in
{
  assertions = [
    {
      assertion =
        parts != null
        && count >= 10
        && count <= (if noDropdowns then 30 else 40)
        && builtins.pathExists file;
      message = "the form task's variant is N or TOOLKIT-N, N from 10 to 40 and TOOLKIT one of toolkits/, or either with -no-dropdowns and N from 10 to 30, not \"${benchVariant}\"";
    }
  ];

  environment.systemPackages = [
    chosen.app
    pkgs.python3
  ]
  ++ (chosen.packages or [ ]);

  environment.etc."hyprmasq-bench/form".source = task;
  environment.etc."hyprmasq-bench/form-count".text = toString count;
  environment.etc."hyprmasq-bench/form-toolkit".text = toolkit;
  environment.etc."hyprmasq-bench/form-kinds".text = kinds;
  environment.etc."hyprmasq-bench/form-popups".text = lib.concatLines (chosen.popups or [ ]);
}
