{
  stdenv,
  python3,
  gtk3,
  gobject-introspection,
  wrapGAppsHook3,
}:
let
  python = python3.withPackages (ps: [
    ps.pyside6
    ps.pygobject3
    ps.tkinter
  ]);
in
stdenv.mkDerivation {
  pname = "hyprmasq-slider-probe";
  version = "1";
  src = ./slider-probe.py;
  dontUnpack = true;
  nativeBuildInputs = [
    wrapGAppsHook3
    gobject-introspection
  ];
  buildInputs = [ gtk3 ];
  installPhase = ''
    install -Dm644 $src $out/share/hyprmasq-slider-probe.py
    mkdir -p $out/bin
    printf '#!%s\nexec %s %s "$@"\n' ${stdenv.shell} ${python}/bin/python3 $out/share/hyprmasq-slider-probe.py > $out/bin/hyprmasq-slider-probe
    chmod +x $out/bin/hyprmasq-slider-probe
  '';
}
