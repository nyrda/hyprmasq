{
  stdenv,
  python3,
  gtk4,
  gobject-introspection,
  wrapGAppsHook4,
}:
let
  python = python3.withPackages (ps: [ ps.pygobject3 ]);
in
stdenv.mkDerivation {
  pname = "hyprmasq-scroll-probe";
  version = "1";
  src = ./scroll-probe.py;
  dontUnpack = true;
  nativeBuildInputs = [
    wrapGAppsHook4
    gobject-introspection
  ];
  buildInputs = [ gtk4 ];
  installPhase = ''
    install -Dm644 $src $out/share/hyprmasq-scroll-probe.py
    mkdir -p $out/bin
    printf '#!%s\nexec %s %s "$@"\n' ${stdenv.shell} ${python}/bin/python3 $out/share/hyprmasq-scroll-probe.py > $out/bin/hyprmasq-scroll-probe
    chmod +x $out/bin/hyprmasq-scroll-probe
  '';
}
