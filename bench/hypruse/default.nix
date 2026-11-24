# hypruse, for running the benchmark tasks with it instead of hyprmasq (bench/default.nix with backend = "hypruse"):
# the release's package from PyPI, unchanged, as `uvx hypruse` runs it, with the programs its README requires on PATH.
#
# `pkgs` is the package set of the VM, whose Hyprland gives hyprctl; by default the nixpkgs of this checkout's
# flake.lock.
{
  pkgs ?
    let
      lock = builtins.fromJSON (builtins.readFile ../../flake.lock);
      locked = lock.nodes.${lock.nodes.root.inputs.nixpkgs}.locked;
    in
    import (builtins.fetchTarball {
      url = "https://github.com/NixOS/nixpkgs/archive/${locked.rev}.tar.gz";
      sha256 = locked.narHash;
    }) { },
  hyprland ? pkgs.hyprland,
}:
let
  inherit (pkgs) lib;
  python = pkgs.python3Packages;
  # Pin mcp 1.x (<2): nixpkgs ships mcp 2.3.0, which wraps non-ToolError Python exceptions into bare
  # "Error executing tool <name>" and strips hypruse's TrustError/ScreenshotError recovery hints.
  mcp1 = python.buildPythonPackage rec {
    pname = "mcp";
    version = "1.30.0";
    pyproject = true;
    src = pkgs.fetchPypi {
      inherit pname version;
      hash = "sha256-RFQUYl/OXClfqlBbsRus7OZhq29AKNV8k121eCC3o+Q=";
    };
    build-system = [
      python.hatchling
      python.uv-dynamic-versioning
    ];
    dependencies = [
      python.anyio
      python.httpx
      python.httpx-sse
      python.jsonschema
      python.pydantic
      python.pydantic-settings
      python.pyjwt
      python.python-multipart
      python.sse-starlette
      python.starlette
      python.typing-extensions
      python.typing-inspection
      python.uvicorn
    ] ++ python.pyjwt.optional-dependencies.crypto;
    doCheck = false;
    pythonImportsCheck = [ "mcp" ];
  };
in
python.buildPythonApplication rec {
  pname = "hypruse";
  # The newest release, 0.15.1 (tag v0.15.1), its sdist as PyPI publishes it.
  version = "0.15.1";
  pyproject = true;

  src = pkgs.fetchPypi {
    inherit pname version;
    hash = "sha256-fPydxj3fb9dS86s7a+sRo00EYYWvBec+gwglhOCIoGo=";
  };

  build-system = [ python.hatchling ];
  # Its one dependency, pinned to mcp 1.x so TrustError/ScreenshotError messages reach the client intact.
  dependencies = [ mcp1 ];

  # The programs its README requires (Hyprland's hyprctl, grim 1.5+ for a window's own pixels, wtype, and busctl for
  # the accessibility bus) and the optional ImageMagick that draws the numbered marks of its `marks` tool: the build with
  # Ghostscript, whose fonts give the marks' numbers their default font, which the build without cannot find. Its other
  # option, wl-clipboard, serves only the clipboard tool, which exists only with HYPRUSE_CLIPBOARD=1.
  #
  # It loads libxkbcommon, for the keys of the user's layout, with ctypes.util.find_library, which finds a library
  # through ldconfig's cache on other distributions; NixOS has none, and Python then looks with ld in
  # LD_LIBRARY_PATH: both are given, so that it finds the library as it would elsewhere.
  libraryPath = lib.makeLibraryPath [ pkgs.libxkbcommon ];
  binPath = lib.makeBinPath [
    hyprland
    pkgs.grim
    pkgs.wtype
    pkgs.systemd
    pkgs.imagemagickBig
    pkgs.binutils-unwrapped
  ];
  makeWrapperArgs = [
    "--prefix"
    "PATH"
    ":"
    binPath
    "--prefix"
    "LD_LIBRARY_PATH"
    ":"
    libraryPath
  ];

  # Its unit tests, which need no compositor, as its CI runs them, with the library found as above. Two compare the
  # skill's path with a checkout's, which the installed package is not.
  nativeCheckInputs = [ python.pytestCheckHook ];
  preCheck = ''
    export PATH=${binPath}:$PATH LD_LIBRARY_PATH=${libraryPath}
  '';
  disabledTests = [
    "test_the_checkout_carries_the_skill_and_it_is_well_formed"
    "test_main_path_prints_the_packaged_directory"
    "test_claude_plugin_runs_its_own_locked_source"
  ];
  pythonImportsCheck = [ "hypruse" ];

  meta = {
    description = "Computer use for Hyprland over MCP";
    homepage = "https://github.com/IlyasKhallouki/hypruse";
    license = lib.licenses.mit;
    mainProgram = "hypruse";
    platforms = lib.platforms.linux;
  };
}
