# The questionnaire as a GTK 3 app in Python (form_gtk3.py), with GTK 3's typelibs, schemas and icons in its
# environment, on Wayland, or with x11 on X11 through Hyprland's XWayland. See vm.nix for what every toolkit's app
# does.
{
  pkgs,
  task,
  launcher,
  xwayland,
  x11 ? false,
  ...
}:
let
  python = pkgs.python3.withPackages (ps: [ ps.pygobject3 ]);
  command = "${python}/bin/python3 ${task}/form_gtk3.py";
in
{
  app = pkgs.stdenv.mkDerivation {
    pname = "hyprmasq-bench-form-gtk3${if x11 then "-x11" else ""}";
    version = "1";
    dontUnpack = true;
    nativeBuildInputs = [
      pkgs.wrapGAppsHook3
      pkgs.gobject-introspection
    ];
    buildInputs = [ pkgs.gtk3 ];
    installPhase = ''
      mkdir -p $out/bin
      ${launcher (
        if x11 then "env GDK_BACKEND=x11 ${xwayland} ${command}" else "env GDK_BACKEND=wayland ${command}"
      )}
    '';
  };
  # On X11, a combo box's menu, of the app's own class.
  popups = if x11 then [ "org.example.Questionnaire" ] else [ ];
}
