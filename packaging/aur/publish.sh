#!/usr/bin/env bash
# Publishes AUR packages: publish.sh DIR..., where each DIR holds a PKGBUILD and the sources that it has beside it, such
# as hyprland-builds. Run it on Arch, with makepkg, git and ssh.
#
# A package is published when one of its files differs from the AUR's, in its PKGBUILD in more than pkgrel. pkgrel is 1
# for a new pkgver and one more than the AUR's for the same. A hyprland-builds whose lines are not what builds.py writes
# stops it, so that only a list of hashes and versions reaches the AUR.
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
line='^([0-9a-f]{40}(_(aq|hu|hg|hc|hlg)_[0-9]+\.[0-9]+){5} [0-9]+\.[0-9]+\.[0-9]+ [0-9]+\.[0-9]+\.[0-9]+-[0-9]+ (reference|identical|accepted)|#.*)$'

for dir in "$@"; do
  (
    cd "$dir"
    if [[ -f hyprland-builds ]] && grep -Evq "$line" hyprland-builds; then
      echo "publish.sh: $dir/hyprland-builds has a line that is no build:" >&2
      grep -Ev "$line" hyprland-builds >&2
      exit 1
    fi
    makepkg --printsrcinfo > "$work/new.SRCINFO"
    pkg=$(sed -n 's/^pkgname = //p' "$work/new.SRCINFO")
    pkgver=$(srcinfo "$work/new.SRCINFO" pkgver)
    aur=$work/$pkg
    git clone --quiet "ssh://aur@aur.archlinux.org/$pkg.git" "$aur" 2> >(grep -v 'empty repository' >&2)
    # The PKGBUILD and the sources it has beside it, rather than downloads.
    mapfile -t files < <(echo PKGBUILD; srcinfo "$work/new.SRCINFO" source | grep -v '://')

    same=true
    for file in "${files[@]}"; do
      if [[ $file == PKGBUILD ]]; then
        [[ -f $aur/PKGBUILD ]] && diff -q <(grep -v '^pkgrel=' PKGBUILD) <(grep -v '^pkgrel=' "$aur/PKGBUILD") > /dev/null || same=false
      else
        cmp -s "$file" "$aur/$file" || same=false
      fi
    done
    if $same; then
      echo "$pkg: the AUR has these files"
      exit 0
    fi

    pkgrel=1
    if [[ -f $aur/.SRCINFO && $(srcinfo "$aur/.SRCINFO" pkgver) == "$pkgver" ]]; then
      pkgrel=$(($(srcinfo "$aur/.SRCINFO" pkgrel) + 1))
    fi
    for file in "${files[@]}"; do
      if [[ $file == PKGBUILD ]]; then
        sed "s/^pkgrel=.*/pkgrel=$pkgrel/" PKGBUILD > "$aur/PKGBUILD"
      else
        cp "$file" "$aur/$file"
      fi
    done
    cd "$aur"
    makepkg --printsrcinfo > .SRCINFO
    git add "${files[@]}" .SRCINFO
    message=("Update to $pkgver-$pkgrel")
    if [[ -n ${GITHUB_SHA:-} ]]; then
      message+=("From $GITHUB_SERVER_URL/$GITHUB_REPOSITORY/commit/$GITHUB_SHA")
    fi
    git -c user.name=nyrda -c user.email=nyrda@keymasq.tools commit --quiet "${message[@]/#/-m}"
    git push --quiet origin HEAD:master
    echo "$pkg: published $pkgver-$pkgrel"
  )
done
