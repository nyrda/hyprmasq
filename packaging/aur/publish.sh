#!/usr/bin/env bash
# Publishes AUR packages: publish.sh DIR..., where each DIR holds a PKGBUILD. Run it on Arch, with makepkg, git and ssh.
#
# A package is published when its PKGBUILD differs from the AUR's in more than pkgrel. pkgrel is 1 for a new pkgver
# and one more than the AUR's for the same.
#
# AUR_SSH_PRIVATE_KEY is the private key of the AUR account that maintains the packages.
set -euo pipefail

if [[ -z ${AUR_SSH_PRIVATE_KEY:-} ]]; then
  echo 'publish.sh: AUR_SSH_PRIVATE_KEY is not set' >&2
  exit 1
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
install -m600 /dev/stdin "$work/key" <<<"$AUR_SSH_PRIVATE_KEY"
# The ED25519 key that https://aur.archlinux.org lists, SHA256:RFzBCUItH9LZS0cKB5UE6ceAYhBD5C8GeOBip8Z11+4.
echo 'aur.archlinux.org ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEuBKrPzbawxA/k2g6NcyV5jmqwJ2s+zpgZGZ7tpLIcN' > "$work/known_hosts"
export GIT_SSH_COMMAND="ssh -i $work/key -o IdentitiesOnly=yes -o UserKnownHostsFile=$work/known_hosts -o StrictHostKeyChecking=yes"

srcinfo() { sed -n "s/^\t$2 = //p" "$1"; }

for dir in "$@"; do
  (
    cd "$dir"
    makepkg --printsrcinfo > "$work/new.SRCINFO"
    pkg=$(sed -n 's/^pkgname = //p' "$work/new.SRCINFO")
    pkgver=$(srcinfo "$work/new.SRCINFO" pkgver)
    aur=$work/$pkg
    git clone --quiet "ssh://aur@aur.archlinux.org/$pkg.git" "$aur" 2> >(grep -v 'empty repository' >&2)

    if [[ -f $aur/PKGBUILD ]] && diff -q <(grep -v '^pkgrel=' PKGBUILD) <(grep -v '^pkgrel=' "$aur/PKGBUILD") > /dev/null; then
      echo "$pkg: the AUR has this PKGBUILD"
      exit 0
    fi

    pkgrel=1
    if [[ -f $aur/.SRCINFO && $(srcinfo "$aur/.SRCINFO" pkgver) == "$pkgver" ]]; then
      pkgrel=$(($(srcinfo "$aur/.SRCINFO" pkgrel) + 1))
    fi
    sed "s/^pkgrel=.*/pkgrel=$pkgrel/" PKGBUILD > "$aur/PKGBUILD"
    cd "$aur"
    makepkg --printsrcinfo > .SRCINFO
    git add PKGBUILD .SRCINFO
    message=("Update to $pkgver-$pkgrel")
    if [[ -n ${GITHUB_SHA:-} ]]; then
      message+=("From $GITHUB_SERVER_URL/$GITHUB_REPOSITORY/commit/$GITHUB_SHA")
    fi
    git -c user.name=nyrda -c user.email=nyrda@keymasq.tools commit --quiet "${message[@]/#/-m}"
    git push --quiet origin HEAD:master
    echo "$pkg: published $pkgver-$pkgrel"
  )
done
