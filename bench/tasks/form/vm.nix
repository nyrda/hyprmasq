# The form task's machine: the questionnaire, a GTK 4 app in Python (form.py). benchVariant, from default.nix, is
# the number of its questions, 10 to 40; items.py has them.
{ pkgs, lib, benchVariant, ... }:
let
  python = pkgs.python3.withPackages (ps: [ ps.pygobject3 ]);
  # The task's files without Python's caches, which a run of the checker on the host may leave.
  task = builtins.path {
    path = ./.;
    name = "hyprmasq-bench-form";
    filter = path: _: baseNameOf path != "__pycache__";
  };
  # form.py with GTK 4's typelibs, schemas and icons in its environment.
  form = pkgs.stdenv.mkDerivation {
    pname = "hyprmasq-bench-form";
    version = "1";
    dontUnpack = true;
    nativeBuildInputs = [
      pkgs.wrapGAppsHook4
      pkgs.gobject-introspection
    ];
    buildInputs = [ pkgs.gtk4 ];
    installPhase = ''
      mkdir -p $out/bin
      cat > $out/bin/hyprmasq-bench-form <<SCRIPT
      #!${pkgs.runtimeShell}
      exec ${python}/bin/python3 ${task}/form.py "\$@"
      SCRIPT
      chmod +x $out/bin/hyprmasq-bench-form
    '';
  };
  count = lib.toIntBase10 benchVariant;
in
{
  assertions = [
    {
      assertion = builtins.match "[0-9]+" benchVariant != null && count >= 10 && count <= 40;
      message = "the form task's variant is its number of questions, 10 to 40, not \"${benchVariant}\"";
    }
  ];

  environment.systemPackages = [
    form
    pkgs.python3
  ];

  environment.etc."hyprmasq-bench/form".source = task;
  environment.etc."hyprmasq-bench/form-count".text = toString count;
}
