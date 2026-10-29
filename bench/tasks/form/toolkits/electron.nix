# The HTML questionnaire (form.html, form_html.py) in Electron, on Wayland. See vm.nix for what every toolkit's app
# does.
args:
let
  html = import ./html args;
in
{
  app = html.app "electron" "wayland" (html.electron "wayland");
}
