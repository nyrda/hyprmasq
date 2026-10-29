# The HTML questionnaire (form.html, form_html.py) in Electron, on X11 through Hyprland's XWayland. See vm.nix for
# what every toolkit's app does.
args:
let
  html = import ./html args;
in
{
  app = html.app "electron-x11" "x11" (html.electron "x11");
}
