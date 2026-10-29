# cefsimple, the sample browser of CEF's source at the revision of nixpkgs' cef-binary, built against it with CEF's
# own CMake files. cef-binary is CEF's minimal distribution, which has no samples. CEF loads its resources from the
# directory of the executable, so lib/cefsimple holds them all next to it.
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
  # The Linux part of tests/cefsimple/CMakeLists.txt.in, which CEF's full distribution has filled in.
  cmakeLists = pkgs.writeText "CMakeLists.txt" ''
    set(CEFSIMPLE_SRCS cefsimple_linux.cc simple_app.cc simple_handler.cc simple_handler_linux.cc)
    set(CEF_TARGET "cefsimple")
    ADD_LOGICAL_TARGET("libcef_lib" "''${CEF_LIB_DEBUG}" "''${CEF_LIB_RELEASE}")
    SET_CEF_TARGET_OUT_DIR()
    add_executable(''${CEF_TARGET} ''${CEFSIMPLE_SRCS})
    SET_EXECUTABLE_TARGET_PROPERTIES(''${CEF_TARGET})
    add_dependencies(''${CEF_TARGET} libcef_dll_wrapper)
    target_link_libraries(''${CEF_TARGET} libcef_lib libcef_dll_wrapper ''${CEF_STANDARD_LIBS})
    set_target_properties(''${CEF_TARGET} PROPERTIES INSTALL_RPATH "$ORIGIN" BUILD_WITH_INSTALL_RPATH TRUE
      RUNTIME_OUTPUT_DIRECTORY ''${CEF_TARGET_OUT_DIR})
    COPY_FILES("''${CEF_TARGET}" "''${CEF_BINARY_FILES}" "''${CEF_BINARY_DIR}" "''${CEF_TARGET_OUT_DIR}")
    COPY_FILES("''${CEF_TARGET}" "''${CEF_RESOURCE_FILES}" "''${CEF_RESOURCE_DIR}" "''${CEF_TARGET_OUT_DIR}")
  '';
in
pkgs.stdenv.mkDerivation {
  pname = "cefsimple";
  inherit (cef) version;
  src = cef;
  nativeBuildInputs = [
    pkgs.cmake
    pkgs.ninja
  ];
  buildInputs = [ pkgs.libx11 ];
  postPatch = ''
    mkdir -p tests
    cp -r ${source}/tests/cefsimple tests/
    chmod -R u+w tests
    cp ${cmakeLists} tests/cefsimple/CMakeLists.txt
    # Only cefsimple of CEF's samples.
    sed -i -e '/add_subdirectory(tests\/\(cefsimple_capi\|gmock\|gtest\|ceftests\))/d' CMakeLists.txt
  '';
  cmakeFlags = [
    "-DPROJECT_ARCH=x86_64"
    # Without the sandbox, which needs a setuid helper.
    "-DUSE_SANDBOX=OFF"
  ];
  ninjaFlags = [ "cefsimple" ];
  # As cef-binary: its libraries keep the search paths of the ones they open at run time.
  dontPatchELF = true;
  dontStrip = true;
  installPhase = ''
    mkdir -p $out/lib $out/bin
    cp -r tests/cefsimple/Release $out/lib/cefsimple
    ln -s $out/lib/cefsimple/cefsimple $out/bin/cefsimple
  '';
}
