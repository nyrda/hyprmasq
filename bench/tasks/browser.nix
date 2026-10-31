# The user's browser in the tasks that have one (board, trace, calc), when benchBrowser, from default.nix, is
# chromium: Chromium with the policies of the Forgejo task's, no first-run screens, sign-in, sync, password prompts
# or promotions. Firefox's configuration stays in each task's vm.nix, as its default browser.
{
  lib,
  pkgs,
  benchBrowser,
  ...
}:
lib.mkIf (benchBrowser == "chromium") {
  programs.chromium = {
    enable = true;
    extraOpts = {
      BrowserSignin = 0;
      SyncDisabled = true;
      PasswordManagerEnabled = false;
      AutofillAddressEnabled = false;
      AutofillCreditCardEnabled = false;
      DefaultBrowserSettingEnabled = false;
      MetricsReportingEnabled = false;
      PromotionsEnabled = false;
      PrivacySandboxPromptEnabled = false;
      TranslateEnabled = false;
      SpellcheckEnabled = false;
    };
  };
  environment.systemPackages = [ pkgs.chromium ];
}
