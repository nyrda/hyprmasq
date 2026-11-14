# Maintainer: nyrda <nyrda@keymasq.tools>

# Builds hyprmasq from this clone: the commit that is checked out, without uncommitted changes. It makes the two
# packages of packaging/aur, hyprmasq-plugin and hyprmasq, and they replace the AUR packages when installed.
# Only Go modules are downloaded, and go.sum verifies them. Run makepkg -sif after every Hyprland update.

pkgbase=hyprmasq
pkgname=(hyprmasq-plugin hyprmasq)
# VERSION, the number of commits and the commit. Set here rather than in pkgver(), which would rewrite this file.
pkgver=$(git show HEAD:VERSION).r$(git rev-list --count HEAD).g$(git rev-parse --short=7 HEAD)
pkgrel=1
pkgdesc='Computer use for Hyprland: per-window screenshots and input, with an MCP server'
arch=(x86_64)
url='https://github.com/nyrda/hyprmasq'
license=(MIT)
# makepkg -s installs these, not the dependencies that package_*() set.
makedepends=(git go hyprland libei libglvnd libpng libxcb libxkbcommon)
# The tests of hyprmasq-mcp record with ffmpeg.
checkdepends=(ffmpeg)

# makepkg works in $BUILDDIR/src, or $BUILDDIR/$pkgbase/src when BUILDDIR is set elsewhere. When that is this clone's
# src, which holds the plugin's sources and which makepkg -c or -C would delete, it works in build/makepkg instead.
if [[ $BUILDDIR -ef $startdir || $BUILDDIR/$pkgbase -ef $startdir ]]; then
  BUILDDIR=$startdir/build/makepkg
fi

# Keeps the modules in $srcdir, uses the installed Go only, and passes makepkg's flags.
_go() {
  GOPATH=$srcdir GOTOOLCHAIN=local GOFLAGS='-buildmode=pie -trimpath -buildvcs=false -mod=readonly -modcacherw' \
    CGO_CPPFLAGS=$CPPFLAGS CGO_CFLAGS=$CFLAGS CGO_CXXFLAGS=$CXXFLAGS CGO_LDFLAGS=$LDFLAGS go "$@"
}

prepare() {
  rm -rf "$pkgbase"
  git -C "$startdir" archive --prefix="$pkgbase/" HEAD | tar -x
  if [[ -n $(git -C "$startdir" status --porcelain) ]]; then
    echo '==> WARNING: Uncommitted changes are left out: makepkg builds the commit that is checked out.' >&2
  fi
  cd "$pkgbase/mcp"
  _go mod download
}

build() {
  cd "$pkgbase"
  make
  cd mcp
  _go build -ldflags "-linkmode=external -X github.com/nyrda/hyprmasq/mcp/internal/server.Version=$(<../VERSION)" ./cmd/hyprmasq-mcp
}

check() {
  cd "$pkgbase/mcp"
  _go test -short ./...
}

# The files and dependencies of the two packages are those of packaging/aur/hyprmasq-plugin and packaging/aur/hyprmasq.
# A change to one changes the other.
package_hyprmasq-plugin() {
  pkgdesc='Hyprland plugin of hyprmasq: per-window screenshots and input'
  depends=(glibc hyprland libei libgcc libglvnd libpng libstdc++ libxcb libxkbcommon)
  cd "$pkgbase"
  install -Dm755 hyprmasq.so hyprmasq-reaper -t "$pkgdir/usr/lib/hyprmasq"
  install -Dm644 /dev/stdin "$pkgdir/etc/xdg/hypr/hyprmasq_plugin.lua" <<<'hl.plugin.load("/usr/lib/hyprmasq/hyprmasq.so")'
  install -Dm644 LICENSE -t "$pkgdir/usr/share/licenses/$pkgname"
}

package_hyprmasq() {
  pkgdesc='MCP server and tools of hyprmasq: computer use for Hyprland'
  depends=(ffmpeg glibc "hyprmasq-plugin=$pkgver" jq libei libxkbcommon sh)
  cd "$pkgbase"
  install -Dm755 hyprmasq-input hyprmasq-launch mcp/hyprmasq-mcp -t "$pkgdir/usr/bin"
  install -Dm644 LICENSE -t "$pkgdir/usr/share/licenses/$pkgname"
}
