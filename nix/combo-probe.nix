{
  stdenv,
  python3,
  gtk3,
  gobject-introspection,
  wrapGAppsHook3,
}:
let
  python = python3.withPackages (ps: [ ps.pygobject3 ]);
in
stdenv.mkDerivation {
  pname = "hyprmasq-combo-probe";
  version = "1";
  src = ./combo-probe.py;
  dontUnpack = true;
  nativeBuildInputs = [
    wrapGAppsHook3
    gobject-introspection
  ];
  buildInputs = [ gtk3 ];
  installPhase = ''
    install -Dm644 $src $out/share/hyprmasq-combo-probe.py
    mkdir -p $out/bin
    printf '#!%s\nexec %s %s "$@"\n' ${stdenv.shell} ${python}/bin/python3 $out/share/hyprmasq-combo-probe.py > $out/bin/hyprmasq-combo-probe
    chmod +x $out/bin/hyprmasq-combo-probe
  '';
}
