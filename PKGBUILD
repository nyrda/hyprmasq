# Maintainer: nyrda <nyrda@keymasq.tools>

# Builds hyprmasq from this clone: the commit that is checked out, without uncommitted changes.
# Only Go modules are downloaded, and go.sum verifies them. Run makepkg -sif after every Hyprland update.

pkgname=hyprmasq
# VERSION, the number of commits and the commit. Set here rather than in pkgver(), which would rewrite this file.
pkgver=$(git show HEAD:VERSION).r$(git rev-list --count HEAD).g$(git rev-parse --short=7 HEAD)
pkgrel=1
pkgdesc='Computer use for Hyprland: per-window screenshots and input, with an MCP server'
arch=(x86_64)
url='https://github.com/nyrda/hyprmasq'
license=(MIT)
depends=(glibc hyprland jq libei libgcc libglvnd libpng libstdc++ libxcb libxkbcommon sh)
makedepends=(git go)

# makepkg works in $BUILDDIR/src, or $BUILDDIR/$pkgname/src when BUILDDIR is set elsewhere. When that is this clone's
# src, which holds the plugin's sources and which makepkg -c or -C would delete, it works in build/makepkg instead.
if [[ $BUILDDIR -ef $startdir || $BUILDDIR/$pkgname -ef $startdir ]]; then
  BUILDDIR=$startdir/build/makepkg
fi

# Keeps the modules in $srcdir, uses the installed Go only, and passes makepkg's flags.
_go() {
  GOPATH=$srcdir GOTOOLCHAIN=local GOFLAGS='-buildmode=pie -trimpath -buildvcs=false -mod=readonly -modcacherw' \
    CGO_CPPFLAGS=$CPPFLAGS CGO_CFLAGS=$CFLAGS CGO_CXXFLAGS=$CXXFLAGS CGO_LDFLAGS=$LDFLAGS go "$@"
}

prepare() {
  rm -rf "$pkgname"
  git -C "$startdir" archive --prefix="$pkgname/" HEAD | tar -x
  if [[ -n $(git -C "$startdir" status --porcelain) ]]; then
    echo '==> WARNING: Uncommitted changes are left out: makepkg builds the commit that is checked out.' >&2
  fi
  cd "$pkgname/mcp"
  _go mod download
}

build() {
  cd "$pkgname"
  make
  cd mcp
  _go build -ldflags "-linkmode=external -X github.com/nyrda/hyprmasq/mcp/internal/server.Version=$(<../VERSION)" ./cmd/hyprmasq-mcp
}

check() {
  cd "$pkgname/mcp"
  _go test -short ./...
}

package() {
  cd "$pkgname"
  install -Dm755 hyprmasq-input hyprmasq-launch mcp/hyprmasq-mcp -t "$pkgdir/usr/bin"
  install -Dm755 hyprmasq.so hyprmasq-reaper -t "$pkgdir/usr/lib/hyprmasq"
  install -Dm644 /dev/stdin "$pkgdir/etc/xdg/hypr/hyprmasq_plugin.lua" <<<'hl.plugin.load("/usr/lib/hyprmasq/hyprmasq.so")'
  install -Dm644 LICENSE -t "$pkgdir/usr/share/licenses/$pkgname"
}
