# cefclient, the full sample browser of CEF's source at the revision of nixpkgs' cef-binary, built against it with
# CEF's own CMake files, which tools/make_cmake.py fills in from cef_paths2.gypi as CEF's full distribution does. It
# can render off-screen: with --off-screen-rendering-enabled, CEF draws into buffers that cefclient paints into its
# GTK 3 window, which it opens on X11, and cefclient sends CEF the window's input. CEF loads its resources from the
# directory of the executable, so lib/cefclient holds them all next to it.
{ pkgs }:
let
  cef = pkgs.cef-binary;
  source =
    assert cef.version == "151.3.16";
    pkgs.fetchFromGitHub {
      owner = "chromiumembedded";
      repo = "cef";
      rev = "be1e15d8892c064f0299ba18350236a9b272ce7f";
      hash = "sha256-mumgd/BMShBjV4Am9tzteveMotlTwdznUcTaGrrIDEQ=";
    };
in
pkgs.stdenv.mkDerivation {
  pname = "cefclient";
  inherit (cef) version;
  src = cef;
  nativeBuildInputs = [
    pkgs.cmake
    pkgs.ninja
    pkgs.pkg-config
    pkgs.python3
  ];
  buildInputs = [
    pkgs.gtk3
    pkgs.glib
    pkgs.libx11
    pkgs.libxi
    pkgs.libxcursor
    pkgs.libGL
  ];
  postPatch = ''
    cp -r ${source} cef-source
    chmod -R u+w cef-source
    # cef_paths.gypi lists CEF's generated API files, which the samples do not use.
    echo "{ 'variables': {} }" > cef-source/cef_paths.gypi
    python3 cef-source/tools/make_cmake.py cef-source/tests/cefclient/CMakeLists.txt.in \
      cef-source/tests/cefclient/CMakeLists.txt
    mkdir -p tests
    cp -r cef-source/tests/cefclient cef-source/tests/shared tests/
    # Only cefclient of CEF's samples.
    sed -i -e '/add_subdirectory(tests\/\(cefsimple\|cefsimple_capi\|gmock\|gtest\|ceftests\))/d' CMakeLists.txt
  '';
  cmakeFlags = [
    "-DPROJECT_ARCH=x86_64"
    # Without the sandbox, which needs a setuid helper.
    "-DUSE_SANDBOX=OFF"
  ];
  ninjaFlags = [ "cefclient" ];
  # As cef-binary: its libraries keep the search paths of the ones they open at run time.
  dontPatchELF = true;
  dontStrip = true;
  installPhase = ''
    mkdir -p $out/lib
    cp -r tests/cefclient/Release $out/lib/cefclient
  '';
}
