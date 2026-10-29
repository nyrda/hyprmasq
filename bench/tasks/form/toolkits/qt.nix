# The questionnaire as a Qt 6 app in Python (form_qt.py, PySide6), on Wayland, with Qt's AT-SPI bridge on from the
# start as GTK's is. See vm.nix for what every toolkit's app does.
{
  pkgs,
  task,
  launcher,
  ...
}:
let
  python = pkgs.python3.withPackages (ps: [ ps.pyside6 ]);
in
{
  app = pkgs.stdenv.mkDerivation {
    pname = "hyprmasq-bench-form-qt";
    version = "1";
    dontUnpack = true;
    installPhase = ''
      mkdir -p $out/bin
      ${launcher "env QT_QPA_PLATFORM=wayland QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 ${python}/bin/python3 ${task}/form_qt.py"}
    '';
  };
}
