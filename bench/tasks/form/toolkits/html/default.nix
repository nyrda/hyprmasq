# What the toolkits of the HTML questionnaire share: form_html.py, which serves form.html on localhost and starts a
# browser engine on it as its child, and the engines. Each toolkit file picks one. See vm.nix for what every
# toolkit's app does.
{
  pkgs,
  task,
  launcher,
  xwayland,
  ...
}:
let
  # The engine's command, which gets the page's URL as its argument: command is a shell line with "$url" in it. On
  # X11 it runs on Hyprland's XWayland.
  browser =
    name: platform: command:
    pkgs.writeShellScript "hyprmasq-bench-form-${name}-browser" ''
      url=$1
      exec ${if platform == "x11" then "${xwayland} " else ""}${command}
    '';

  electronApp = pkgs.runCommand "hyprmasq-bench-form-electron-app" { } ''
    mkdir -p $out
    cp ${./main.js} $out/main.js
    echo '{ "name": "questionnaire", "main": "main.js" }' > $out/package.json
  '';
in
{
  # The app of a toolkit: its name, such as electron-x11, its platform, wayland or x11, and the engine's command.
  app =
    name: platform: command:
    pkgs.runCommand "hyprmasq-bench-form-${name}" { } ''
      mkdir -p $out/bin
      ${launcher "${pkgs.python3}/bin/python3 ${task}/form_html.py --browser ${browser name platform command}"}
    '';

  # Electron, on Wayland or X11, with the page in its one window.
  electron =
    platform: "${pkgs.electron}/bin/electron --ozone-platform=${platform} ${electronApp} \"$url\"";

  # cefsimple, CEF's sample browser, with the page in its one window, which CEF's Views framework draws. Alloy
  # style is CEF's own minimal browser, without Chrome's UI and shortcuts.
  cef =
    platform:
    "${
      import ./cefsimple.nix { inherit pkgs; }
    }/lib/cefsimple/cefsimple --ozone-platform=${platform} --use-alloy-style --url=\"$url\"";

  # cefclient, CEF's full sample browser, rendering off-screen: CEF draws into buffers that cefclient paints into its
  # GTK 3 window, on X11, as a game's overlay or the Steam client can. Its toolbar and menu are hidden.
  cefOffScreen = "${
    import ./cefclient.nix { inherit pkgs; }
  }/lib/cefclient/cefclient --off-screen-rendering-enabled --use-alloy-style --hide-controls --hide-top-menu --url=\"$url\"";
}
