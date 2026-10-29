# The questionnaire as a Tk app in Python (form_tk.py, tkinter). Tk only runs on X11, so it runs on Hyprland's
# XWayland. See vm.nix for what every toolkit's app does.
{
  pkgs,
  task,
  launcher,
  xwayland,
  ...
}:
let
  python = pkgs.python3.withPackages (ps: [ ps.tkinter ]);
in
{
  app = pkgs.stdenv.mkDerivation {
    pname = "hyprmasq-bench-form-tk";
    version = "1";
    dontUnpack = true;
    installPhase = ''
      mkdir -p $out/bin
      ${launcher "${xwayland} ${python}/bin/python3 ${task}/form_tk.py"}
    '';
  };
  # A combo box's list.
  popups = [ "ComboboxPopdown" ];
}
