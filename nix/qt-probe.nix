{ writeShellScriptBin, python3 }:
let
  python = python3.withPackages (ps: [ ps.pyside6 ]);
in
writeShellScriptBin "hyprmasq-qt-probe" ''
  exec ${python}/bin/python3 ${./qt-probe.py} "$@"
''
