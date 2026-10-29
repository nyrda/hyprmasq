# The HTML questionnaire (form.html, form_html.py) in a CEF browser, cefsimple, on Wayland. See vm.nix for what
# every toolkit's app does.
args:
let
  html = import ./html args;
in
{
  app = html.app "cef" "wayland" (html.cef "wayland");
}
