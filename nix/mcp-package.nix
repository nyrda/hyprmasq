# hyprmasq-mcp, the MCP server that gives agents computer use through hyprmasq. It talks to the plugin through
# Hyprland's socket and runs hyprmasq-input from PATH, so it needs the plugin package of the same revision.
# package.nix installs it as $out/bin/hyprmasq-mcp with that hyprmasq-input on its PATH.
{
  lib,
  buildGoModule,
}:

buildGoModule {
  pname = "hyprmasq-mcp";
  version = "0.3.0";

  src = lib.cleanSource ../mcp;
  vendorHash = "sha256-NbuvbsmNbz3aKdjyyfpkbi52yRhXl473j3LvAUDn3L8=";
  # Every package is built and tested; only the command is installed.
  env.CGO_ENABLED = 0;

  meta = {
    description = "MCP server for driving Hyprland windows through the hyprmasq plugin";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "hyprmasq-mcp";
  };
}
