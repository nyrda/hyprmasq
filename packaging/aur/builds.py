#!/usr/bin/env python3
"""The Hyprland builds of Arch and Omarchy that the prebuilt plugins of hyprmasq-plugin-bin run on.

A Hyprland plugin must be compiled against headers whose layout matches the running Hyprland, and hyprmasq refuses any
other build unless the hyprland-builds file next to it names that build. This script decides which builds go in that
file. It runs as root in an Arch container (archlinux:base-devel), and changes /usr/include there.

  builds.py served                      the Hyprland builds that Arch and Omarchy serve, of the releases hyprmasq targets
  builds.py references                  the build each prebuilt plugin is compiled against: per release, the one served
                                        to the oldest userland
  builds.py plan [--list FILE]          the prebuilt plugins with their references, snapshots and userlands, and the
                                        served builds that the list lacks
  builds.py snapshot VERSION            the first archive.archlinux.org snapshot that has VERSION, and the archlinux image
                                        from before it to build on
  builds.py verify --source DIR --binary LABEL=FILE... [--list FILE] [--accept VERSION] [--candidate VERSION]...
            --out FILE --held FILE
                                        verifies every served build against the prebuilt plugins, and writes the new list

A served build goes in the list when the plugin compiled against its headers is byte for byte the plugin compiled
against the headers of the reference build (same toolchain, same version.h, no build id), when every symbol that the
shipped plugin imports or hooks is exported by that Hyprland or its libraries, and when its Hyprland commit is the
reference's, or the source files of every hooked function and of every class whose private members the plugin reads
are unchanged since then. A build that fails one of them is held, unless --accept names it, which waives the source
check only.

hyprland-builds has one line per build: the hash that __hyprland_api_get_hash() returns, the label of the plugin that
runs it (the Hyprland version of its reference), the build's version, and how it got there: reference, identical or
accepted.
"""

import argparse
import datetime
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ARCHIVE = "https://archive.archlinux.org"
OMARCHY_STABLE = "https://stable-mirror.omarchy.org"
# The libraries whose major.minor __hyprland_api_get_hash() names, with the prefix it gives them.
ABI_LIBRARIES = [("aquamarine", "aq"), ("hyprutils", "hu"), ("hyprgraphics", "hg"), ("hyprcursor", "hc"), ("hyprlang", "hlg")]
WORK = Path(os.environ.get("BUILDS_WORK", "/var/tmp/hyprmasq-builds"))
REPO = Path(__file__).resolve().parents[2]


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "hyprmasq-ci"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def releases():
    """The Hyprland releases that hyprmasq targets, as major.minor."""
    return [".".join(line.split(".")[:2]) for line in (REPO / "HYPRLAND_VERSION").read_text().split()]


def minor(version):
    return ".".join(version.split("-")[0].split(".")[:2])


def served():
    """[{version, sources}] of hyprland, for the releases hyprmasq targets."""
    found = {}
    data = json.loads(fetch("https://archlinux.org/packages/search/json/?name=hyprland&arch=x86_64"))
    for result in data["results"]:
        found.setdefault(f"{result['pkgver']}-{result['pkgrel']}", []).append(result["repo"])
    # Omarchy's stable channel installs Arch's packages from a dated snapshot. Its database only tells the version: the
    # package itself comes from the Arch archive, where verify() checks its signature.
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "extra.db"
        db.write_bytes(fetch(f"{OMARCHY_STABLE}/extra/os/x86_64/extra.db"))
        names = run("tar", "-tzf", db, capture_output=True).stdout.split()
    for name in names:
        if m := re.fullmatch(r"hyprland-(\d+\.\d+\.\d+-\d+)/", name):
            found.setdefault(m.group(1), []).append("omarchy-stable")
    targets = releases()
    builds = [{"version": v, "sources": sorted(s)} for v, s in found.items() if minor(v) in targets]
    return sorted(builds, key=lambda b: version_key(b["version"]), reverse=True)


def version_key(version):
    ver, _, rel = version.partition("-")
    return tuple(int(p) for p in ver.split(".")) + (int(rel or 0),)


def references(builds):
    """{label: version}: per release, the build that the oldest userland runs, Omarchy's stable channel before Arch."""
    chosen = {}
    for release in releases():
        of = [b for b in builds if minor(b["version"]) == release]
        if not of:
            continue
        oldest = [b for b in of if "omarchy-stable" in b["sources"]] or of
        version = min((b["version"] for b in oldest), key=version_key)
        chosen[version.split("-")[0]] = version
    return chosen


@functools.cache
def archive_listing(name):
    return fetch(f"{ARCHIVE}/packages/{name[0]}/{name}/").decode()


def archive_file(name, version):
    """The package file of name at version, or at the highest pkgrel of version when it has none."""
    listing = archive_listing(name)
    if "-" in version:
        files = [f"{name}-{version}-x86_64.pkg.tar.zst"]
    else:
        files = sorted(set(re.findall(rf'"({re.escape(name)}-{re.escape(version)}-(\d+)-x86_64\.pkg\.tar\.zst)"', listing)), key=lambda m: int(m[1]))
        files = [files[-1][0]] if files else []
    if not files or f'"{files[0]}"' not in listing:
        raise SystemExit(f"{name} {version} is not in the Arch archive")
    return files[0]


def snapshot(version):
    """{day, image}: the first archive.archlinux.org snapshot whose extra lists hyprland-VERSION, and the newest
    archlinux/archlinux base-devel image from before it, which pacman upgrades to that snapshot."""
    file = archive_file("hyprland", version)
    m = re.search(rf'"{re.escape(file)}">[^<]*</a>\s+(\d\d-[A-Za-z]{{3}}-\d{{4}})', archive_listing("hyprland"))
    if not m:
        raise SystemExit(f"no date for {file}")
    day = datetime.datetime.strptime(m.group(1), "%d-%b-%Y").date()
    for _ in range(21):
        path = day.strftime("%Y/%m/%d")
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "extra.db"
            db.write_bytes(fetch(f"{ARCHIVE}/repos/{path}/extra/os/x86_64/extra.db"))
            if f"hyprland-{version}/" in run("tar", "-tzf", db, capture_output=True).stdout.split():
                break
        day += datetime.timedelta(days=1)
    else:
        raise SystemExit(f"no snapshot lists hyprland {version}")
    stamp = day.strftime("%Y%m%d")
    tags = json.loads(fetch(f"https://hub.docker.com/v2/repositories/archlinux/archlinux/tags?page_size=100&name=base-devel-{stamp[:6]}"))["results"]
    tags += json.loads(fetch(f"https://hub.docker.com/v2/repositories/archlinux/archlinux/tags?page_size=100&name=base-devel-{(day.replace(day=1) - datetime.timedelta(days=1)).strftime('%Y%m')}"))["results"]
    images = sorted(t["name"] for t in tags if re.fullmatch(r"base-devel-\d{8}\.\d+\.\d+", t["name"]) and t["name"][11:19] <= stamp)
    if not images:
        raise SystemExit(f"no archlinux image from before {stamp}")
    return {"day": path, "image": images[-1]}


def package(name, version):
    """Downloads a package from the Arch archive, checks its signature, and returns its extracted root."""
    file = archive_file(name, version)
    root = WORK / "packages" / file.removesuffix(".pkg.tar.zst")
    if (root / ".done").exists():
        return root
    root.mkdir(parents=True, exist_ok=True)
    url = f"{ARCHIVE}/packages/{name[0]}/{name}/{file}"
    archive = WORK / "packages" / file
    archive.write_bytes(fetch(url))
    signature = Path(f"{archive}.sig")
    signature.write_bytes(fetch(url + ".sig"))
    run("pacman-key", "--verify", signature, archive, capture_output=True)
    run("tar", "--zstd", "-xf", archive, "-C", root, "usr")
    (root / ".done").touch()
    return root


def version_h(root):
    text = (root / "usr/include/hyprland/src/version.h").read_text()
    return {m.group(1): m.group(2) for m in re.finditer(r'#define (\w+)\s+"([^"]*)"', text)}


class Build:
    """A Hyprland build of the archive: its headers and binary, with those of the libraries it names in version.h."""

    def __init__(self, version):
        self.version = version
        self.root = package("hyprland", version)
        defines = version_h(self.root)
        self.commit = defines["GIT_COMMIT_HASH"]
        self.libraries = {name: package(name, defines[f"{name.upper()}_VERSION"]) for name, _ in ABI_LIBRARIES}
        strip = lambda v: v.rsplit(".", 1)[0] if "." in v else v
        self.abi = self.commit + "".join(f"_{prefix}_{strip(defines[f'{name.upper()}_VERSION'])}" for name, prefix in ABI_LIBRARIES)

    def install_headers(self, version_h_from=None):
        """Puts these headers in /usr/include, over the installed ones."""
        for root in [self.root, *self.libraries.values()]:
            for entry in (root / "usr/include").iterdir():
                target = Path("/usr/include") / entry.name
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                elif target.exists() or target.is_symlink():
                    target.unlink()
                (shutil.copytree if entry.is_dir() else shutil.copy2)(entry, target)
        if version_h_from:
            shutil.copy2(version_h_from.root / "usr/include/hyprland/src/version.h", "/usr/include/hyprland/src/version.h")

    def exports(self):
        """The dynamic symbols that this Hyprland exports, with the libraries of its version.h and the other libraries
        it loads, as the container has them."""
        binary = self.root / "usr/bin/Hyprland"
        files = [binary] + [f for root in self.libraries.values() for f in (root / "usr/lib").glob("*.so*") if f.is_file()]
        return defined_symbols(files + loaded(binary))


def loaded(binary):
    """The shared libraries that the dynamic loader resolves for binary in this container, and the loader itself."""
    out = run("ldd", binary, capture_output=True).stdout
    return [Path(p) for p in re.findall(r"(/\S+\.so[.\d]*)", out)]


def defined_symbols(files):
    names = set()
    for file in files:
        out = run("nm", "-D", "--defined-only", file, capture_output=True).stdout
        names.update(line.split()[-1].split("@")[0] for line in out.splitlines() if line.strip())
    return names


def compile_plugin(source, build, version_h_from, out):
    """The plugin compiled against build's headers with version_h_from's version.h, stripped and without a build id."""
    build.install_headers(version_h_from)
    tree = WORK / "tree"
    if tree.exists():
        shutil.rmtree(tree)
    shutil.copytree(source, tree, ignore=shutil.ignore_patterns(".git", "build", "*.so", "result*"))
    env = dict(os.environ, CXXFLAGS="-O2 -g0", LDFLAGS="-Wl,--build-id=none")
    result = subprocess.run(["make", "-s", f"-j{os.cpu_count()}", "hyprmasq.so", "IGNORE_HYPRLAND_VERSION=1"], cwd=tree, env=env, text=True, capture_output=True)
    if result.returncode != 0:
        return None, result.stderr[-4000:]
    run("strip", tree / "hyprmasq.so")
    shutil.copy2(tree / "hyprmasq.so", out)
    return hashlib.sha256(Path(out).read_bytes()).hexdigest(), ""


def hooked(binary):
    """The functions the plugin hooks: the mangled names it looks up, which it keeps as string literals in .rodata. The
    symbol table's names are elsewhere."""
    with tempfile.TemporaryDirectory() as tmp:
        rodata = Path(tmp) / "rodata"
        run("objcopy", "-O", "binary", "--only-section=.rodata", binary, rodata)
        strings = run("strings", "-n", "8", rodata, capture_output=True).stdout.split()
    return sorted({s for s in strings if re.fullmatch(r"_ZN[0-9A-Za-z_]+", s)})


def missing_symbols(binary, build):
    """What the shipped plugin imports or hooks that the build's Hyprland and the libraries do not export."""
    needed = run("nm", "-D", "--undefined-only", binary, capture_output=True).stdout
    imports = {line.split()[-1].split("@")[0] for line in needed.splitlines() if line.split()[0] not in ("w", "v")}
    exported = build.exports() | defined_symbols(loaded(binary))
    return sorted((imports - exported) | (set(hooked(binary)) - exported))


@functools.cache
def hyprland_tree(commit):
    root = WORK / "hyprland-src" / commit
    if not root.exists():
        root.mkdir(parents=True)
        archive = WORK / "hyprland-src" / f"{commit}.tar.gz"
        archive.write_bytes(fetch(f"https://github.com/hyprwm/Hyprland/archive/{commit}.tar.gz"))
        run("tar", "-xzf", archive, "-C", root, "--strip-components=1")
    return root


def definition(tree, qualified):
    """The source file under src/ that defines the function Namespace::Class::function, or None unless there is exactly
    one. Hyprland writes a definition inside its namespace, so Class::function is what it names."""
    pattern = re.compile(rf"^[^\s/#].*\b{re.escape('::'.join(qualified.split('::')[-2:]))}\(", re.M)
    files = [p for p in (tree / "src").rglob("*.cpp") if pattern.search(p.read_text(errors="replace"))]
    return files[0].relative_to(tree) if len(files) == 1 else None


def class_source(tree, qualified):
    """The .cpp beside the header that defines the class, if it has one."""
    name = qualified.split("::")[-1]
    pattern = re.compile(rf"^\s*(class|struct)\s+{re.escape(name)}\b[^;]*$", re.M)
    for header in (tree / "src").rglob("*.hpp"):
        if pattern.search(header.read_text(errors="replace")):
            cpp = header.with_suffix(".cpp")
            return cpp.relative_to(tree) if cpp.exists() else None
    return None


def private_classes(source, build):
    """The classes whose private members PrivateAccess.cpp reaches, for this build's headers."""
    build.install_headers()
    flags = run("pkg-config", "--cflags", "hyprland", "libdrm", "pixman-1", "egl", "glesv2", "libpng", "libeis-1.0", "xkbcommon", "xcb", capture_output=True).stdout.split()
    out = run("g++", "-std=c++2b", "-E", "-DWLR_USE_UNSTABLE", *flags, source / "src/PrivateAccess.cpp", capture_output=True).stdout
    return sorted(set(re.findall(r"SExpose(?:Unnamed)?<\s*\w+\s*,\s*&\s*((?:\w+::)*\w+)::\w+\s*>", out)))


def source_changes(source, binary, reference, build):
    """The source files that a hooked function or a reached class lives in, which changed between the two commits.
    Fails closed: a function found in no file, in several, or in another file than at the reference is a change."""
    old, new = hyprland_tree(reference.commit), hyprland_tree(build.commit)
    files, changed = set(), []
    for mangled in hooked(binary):
        signature = run("c++filt", mangled, capture_output=True).stdout.strip()
        qualified = signature.split("(")[0]
        before, after = definition(old, qualified), definition(new, qualified)
        if not before or before != after:
            changed.append(f"{qualified}: defined in {before} at the reference and {after} now")
        else:
            files.add(before)
    for qualified in private_classes(source, build):
        before, after = class_source(old, qualified), class_source(new, qualified)
        if before != after:
            changed.append(f"{qualified}: its source is {before} at the reference and {after} now")
        elif before:
            files.add(before)
    for file in sorted(files):
        if (old / file).read_bytes() != (new / file).read_bytes():
            changed.append(f"{file} changed")
    return changed


def read_list(path):
    entries = []
    if path and Path(path).exists():
        for line in Path(path).read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                abi, label, version, how = line.split()
                entries.append({"abi": abi, "label": label, "version": version, "how": how})
    return entries


def write_list(path, entries):
    lines = ["# hash label version how, see packaging/aur/builds.py of github.com/nyrda/hyprmasq"]
    for e in sorted(entries, key=lambda e: (e["label"], version_key(e["version"]))):
        lines.append(f"{e['abi']} {e['label']} {e['version']} {e['how']}")
    Path(path).write_text("\n".join(lines) + "\n")


def verify(args):
    WORK.mkdir(parents=True, exist_ok=True)
    source = Path(args.source).resolve()
    binaries = dict(b.split("=", 1) for b in args.binary)
    listed = read_list(args.list)
    builds = served()
    # The reference of each plugin: from the list when it has one, else the build that references() chooses now.
    refs = {e["label"]: e["version"] for e in listed if e["how"] == "reference"} or references(builds)
    builds += [{"version": v, "sources": ["asked"]} for v in args.candidate]
    missing = set(binaries) ^ set(refs)
    if missing:
        raise SystemExit(f"the plugins {sorted(binaries)} and the references {sorted(refs)} differ")
    entries = {e["version"]: e for e in listed}
    held = []
    for label, version in refs.items():
        reference = Build(version)
        entries[version] = {"abi": reference.abi, "label": label, "version": version, "how": "reference"}
        expected, error = compile_plugin(source, reference, reference, WORK / f"reference-{label}.so")
        if not expected:
            raise SystemExit(f"the plugin does not compile against its reference {version}:\n{error}")
        accepted = {Build(e["version"]).commit for e in entries.values() if e["label"] == label and e["how"] in ("reference", "accepted")}
        for b in builds:
            if minor(b["version"]) != minor(version) or b["version"] == version:
                continue
            build = Build(b["version"])
            reasons = []
            got, error = compile_plugin(source, build, reference, WORK / f"candidate-{b['version']}.so")
            if got != expected:
                reasons.append("the plugin compiles to other code against its headers" + (f": {error}" if error else ""))
            if missing_syms := missing_symbols(binaries[label], build):
                reasons.append(f"its Hyprland or libraries do not export {', '.join(missing_syms[:10])}")
            how = "identical"
            if not reasons and build.commit not in accepted:
                if changes := source_changes(source, binaries[label], reference, build):
                    if args.accept == b["version"]:
                        how = "accepted"
                    else:
                        reasons.append("Hyprland's commit moved and these sources changed: " + "; ".join(changes[:20]))
            if reasons:
                if b["version"] in entries:
                    raise SystemExit(f"{b['version']} is in the list, but {'; '.join(reasons)}")
                held.append({"version": b["version"], "label": label, "abi": build.abi, "sources": b["sources"], "reasons": reasons})
                log(f"held {b['version']}: {reasons}")
            else:
                entries[b["version"]] = {"abi": build.abi, "label": label, "version": b["version"], "how": how}
                log(f"{how} {b['version']}")
        if missing_syms := missing_symbols(binaries[label], reference):
            raise SystemExit(f"the plugin {label} imports what its reference {version} does not export: {missing_syms[:10]}")
    write_list(args.out, entries.values())
    Path(args.held).write_text(json.dumps(held, indent=2) + "\n")


def plan(list_path):
    """What CI builds and checks: per prebuilt plugin its label, reference build, snapshot, image and the userlands that
    run it, and the served builds that the list does not have yet."""
    builds = served()
    listed = read_list(list_path)
    refs = {e["label"]: e["version"] for e in listed if e["how"] == "reference"} or references(builds)
    plugins = []
    for label, version in sorted(refs.items()):
        sources = {s for b in builds if minor(b["version"]) == minor(version) for s in b["sources"]}
        userlands = (["arch"] if sources - {"omarchy-stable"} else []) + (["omarchy-stable"] if "omarchy-stable" in sources else [])
        plugins.append({"label": label, "version": version, **snapshot(version), "userlands": userlands})
    known = {e["version"] for e in listed}
    return {"plugins": plugins, "new": [b["version"] for b in builds if b["version"] not in known]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("served")
    p = sub.add_parser("plan")
    p.add_argument("--list", help="the hyprland-builds that the AUR has")
    sub.add_parser("references")
    p = sub.add_parser("snapshot")
    p.add_argument("version")
    p = sub.add_parser("verify")
    p.add_argument("--source", required=True)
    p.add_argument("--binary", action="append", required=True, help="LABEL=FILE of a prebuilt plugin")
    p.add_argument("--list", help="the hyprland-builds that the AUR has")
    p.add_argument("--accept", help="a build whose Hyprland sources changed, accepted by hand")
    p.add_argument("--candidate", action="append", default=[], help="a build to verify as if it were served")
    p.add_argument("--out", required=True)
    p.add_argument("--held", required=True)
    args = parser.parse_args()
    if args.command == "served":
        print(json.dumps(served(), indent=2))
    elif args.command == "references":
        print(json.dumps(references(served())))
    elif args.command == "plan":
        print(json.dumps(plan(args.list)))
    elif args.command == "snapshot":
        print(json.dumps(snapshot(args.version)))
    else:
        verify(args)


if __name__ == "__main__":
    main()
