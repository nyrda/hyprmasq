# The questionnaire as a GTK 4 app in Python (form.py), with GTK 4's typelibs, schemas and icons in its
# environment. See vm.nix for what every toolkit's app does.
{
  pkgs,
  task,
  launcher,
  ...
}:
let
  python = pkgs.python3.withPackages (ps: [ ps.pygobject3 ]);
in
{
  app = pkgs.stdenv.mkDerivation {
    pname = "hyprmasq-bench-form-gtk4";
    version = "1";
    dontUnpack = true;
    nativeBuildInputs = [
      pkgs.wrapGAppsHook4
      pkgs.gobject-introspection
    ];
    buildInputs = [ pkgs.gtk4 ];
    installPhase = ''
      mkdir -p $out/bin
      ${launcher "${python}/bin/python3 ${task}/form.py"}
    '';
  };
}
