## What it does

<!-- The change as it is now: what it does and why. -->

## How it was tested

<!-- CI evaluates the flake, builds the plugin and MCP server, runs all Go tests, checks
C/C++ and Go formatting, and runs the bench unit tests including Calc/LibreOffice.
Record any additional checks here. -->

## Test gate

CI runs the checks listed above. The test gate is local and runs on the author's machine on the PR's head commit before the PR is opened,
and again before it is merged whenever the head commit is no longer the one it passed on, after new commits, an amend,
a rebase or a force-push. It runs the container test on a machine with a GPU and the Nix settings in `docs/vm-test.md`, and otherwise the VM test:

```sh
./dev.sh gate
```

- [ ] Passed on the PR's head commit: <!-- commit hash, and the container test or the VM test -->
- [ ] Before merging: the head commit is still the one above, or the test passed again on it: <!-- commit hash, and the container test or the VM test -->

A PR that changes how the test itself runs in the VM or the container passes both. A PR that changes none of the plugin, its tools, the MCP server, the NixOS module and the test, such as one of docs or `bench/` alone, skips the gate and says so here.

## Notes for reviewers

<!-- What a reviewer must know. Remove this section when there is nothing. -->
