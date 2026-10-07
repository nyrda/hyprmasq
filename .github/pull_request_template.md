## What it does

<!-- The change as it is now: what it does and why. -->

## How it was tested

<!-- Go tests, the plugin build, and what was checked by hand. -->

## VM test

CI does not run the NixOS VM test, so it runs on the author's machine on the PR's head commit before the PR is opened,
and again before it is merged whenever the head commit is no longer the one it passed on, after new commits, an amend,
a rebase or a force-push:

```sh
nix build .#checks.x86_64-linux.vm-test -L
```

- [ ] Passed on the PR's head commit: <!-- commit hash -->
- [ ] Before merging: the head commit is still the one above, or the test passed again on it: <!-- commit hash -->

A PR that changes only Markdown files skips it and says so here: neither the VM test nor its Nix build reads them.

## Notes for reviewers

<!-- What a reviewer must know. Remove this section when there is nothing. -->
