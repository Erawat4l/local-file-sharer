"""Build a single-file installer that carries the source inside it.

Cloning a private repository needs a credential the phone does not have, and
retyping the owner name is a coin flip: it ends in a lowercase L that reads as
a digit 1. `install-lfs.py` carries the whole checkout, so getting the app onto
a device needs no git, no token and no URL.

    python3 make_installer.py --version 1.0.0 --ref 7ad9764 --out install-lfs.py
"""

from __future__ import annotations

import argparse
import base64
import gzip
import io
import pathlib
import subprocess
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Nothing here is needed to run the app, and the screenshots alone are 2.3 MB.
SKIP_DIRS = {"docs", "tests", ".github", ".git", "build", "dist"}
SKIP_SUFFIXES = {".png", ".gif", ".jpg", ".jpeg", ".webp"}
# The built installer is tracked at the repo root, so without this it would
# bundle its previous self and double in size on every rebuild.
SKIP_FILES = {"install-lfs.py"}


def collect(root: pathlib.Path) -> bytes:
    """A .tar.gz of every tracked file the app needs, rooted at local-file-sharer/."""
    listed = subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        check=True,
    ).stdout
    names = [n for n in listed.decode().split("\0") if n]

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name in names:
            path = root / name
            parts = pathlib.PurePosixPath(name).parts
            if not path.is_file() or parts[0] in SKIP_DIRS:
                continue
            if name in SKIP_FILES:
                continue
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            tar.add(path, arcname=f"local-file-sharer/{name}")
    packed = io.BytesIO()
    with gzip.GzipFile(fileobj=packed, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(raw.getvalue())
    return packed.getvalue()


TEMPLATE = '''#!/usr/bin/env python3
"""Install LOCAL FILE SHARER from this one file — no GitHub, no git, no token.

The repository is private, so `pip install git+https://…` cannot work on a
device without credentials. Everything the app needs is inside this file.

    python3 {name}              install (or upgrade) the `lfs` command
    python3 {name} --check      show what is installed
    python3 {name} --uninstall  remove it again
"""

import base64
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

VERSION = {version!r}
REF = {ref!r}
ARCHIVE = {archive!r}

BANNER = "LOCAL FILE SHARER"


def say(text=""):
    print(text, flush=True)


def have(cmd):
    return shutil.which(cmd) is not None


def unpack():
    """Write the embedded source to a temp dir and return its path."""
    raw = base64.b64decode(ARCHIVE)
    target = tempfile.mkdtemp(prefix="lfs-install-")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        try:
            tar.extractall(target, filter="data")   # Python 3.12+
        except TypeError:
            tar.extractall(target)                   # 3.9 - 3.11
    return os.path.join(target, "local-file-sharer")


def run(cmd, **kw):
    say("  $ " + " ".join(cmd))
    return subprocess.call(cmd, **kw)


def check():
    try:
        from importlib.metadata import version

        installed = version("local-file-sharer")
    except Exception:
        say("  lfs is not installed")
        return 1
    say(f"  installed {{installed}}  (this file carries {{VERSION}} @ {{REF}})")
    return 0


def uninstall():
    """Remove every trace this file may have left.

    The old version ran `pip uninstall` and printed "the `lfs` command is
    gone", which was often untrue: this installer creates a directory beside
    the user with a symlink on PATH, and pip never knows about either. Removing
    pip's copy of a package it did not install is also a no-op, so the message
    was optimistic at best.
    """
    removed_any = False

    # 1. pipx, if it made one. `have` only says the tool exists, so ask it
    #    whether this package is actually one of its venvs first -- otherwise
    #    every uninstall prints "not installed" noise about someone else's
    #    tooling.
    if have("pipx") and run(["pipx", "list", "--short"]) is not None:
        listing = subprocess.run(["pipx", "list", "--short"], capture_output=True, text=True)  # noqa: S603
        if "local-file-sharer" in (listing.stdout or ""):
            if run(["pipx", "uninstall", "local-file-sharer"]) == 0:
                removed_any = True

    # 2. pip, if it made one
    if have("pip"):
        for flags in (["--user"], [], ["--break-system-packages"]):
            probe = subprocess.run(  # noqa: S603
                [sys.executable, "-m", "pip", "show", "local-file-sharer"],
                capture_output=True,
                text=True,
            )
            if probe.returncode == 0:
                if run([sys.executable, "-m", "pip", "uninstall", "-y", *flags, "local-file-sharer"]) == 0:
                    removed_any = True
                break

    # 3. the checked-out tree this installer unpacks, and every symlink to it
    home = os.path.expanduser("~")
    target = os.path.join(home, "local-file-sharer")
    binary = os.path.join(target, ".venv", "bin", "lfs")
    for bindir in (os.path.join(os.environ.get("PREFIX", ""), "bin"), os.path.join(home, ".local", "bin"),
                   os.path.join(home, "bin")):
        if not bindir.strip(os.sep):
            continue
        link = os.path.join(bindir, "lfs")
        if os.path.islink(link):
            try:
                if os.path.realpath(link) == os.path.realpath(binary) or target in os.path.realpath(link):
                    os.unlink(link)
                    say(f"  removed {{link}}")
                    removed_any = True
            except OSError as exc:
                say(f"  could not remove {{link}}: {{exc}}")

    if os.path.isdir(target):
        # a shutdown helper, not a self-delete: the folder may be the one this
        # process is running from, and that cannot be rmtree'd safely
        if run(["/bin/sh", "-c", f'sleep 1; rm -rf "{{target}}"']) == 0:
            say(f"  removed {{target}}")
            removed_any = True

    if removed_any:
        say("  removed. the `lfs` command is gone; your files were not touched.")
        return 0
    say("  nothing to remove — no lfs install was found here.")
    return 0


def install():
    """Unpack the checkout next to the user, then let ./setup build it.

    The install deliberately lives in a plain directory rather than
    site-packages: it is the same layout a `git clone` gives, so ./run, ./setup,
    and `git pull` all work exactly as the documentation says.
    """
    say(f"\\n  {{BANNER}}  {{VERSION}}  ({{REF}})\\n")
    source = unpack()

    target = os.environ.get("LFS_HOME") or os.path.join(os.path.expanduser("~"), "local-file-sharer")
    if os.path.exists(os.path.join(target, "src")):
        # Re-installing over a good checkout is refused rather than done: the
        # user's config lives outside it, but a half-merged source tree would
        # be worse than a clear message.
        say(f"  already installed at {{target}}")
        say(f"  upgrade it instead:  cd {{target}} && ./run upgrade")
        return 0
    if not os.path.exists(target):
        shutil.move(source, target)
    else:
        # directory exists but empty: copy the contents across
        for name in os.listdir(source):
            shutil.move(os.path.join(source, name), os.path.join(target, name))
        shutil.rmtree(source, ignore_errors=True)

    say(f"  unpacked to {{target}}")
    say("")
    setup = os.path.join(target, "setup")
    if os.access(setup, os.X_OK):
        code = run([setup])
        if code != 0:
            say("\\n  ./setup failed — read its output above.\\n")
            return code
    # Create the `lfs` command, not just a folder with a ./run in it. A
    # newcomer who finishes installing and is told to `cd` into a directory and
    # type `./run` every time has not installed a command -- they have unpacked
    # a folder and been handed a chore. ./run install links it into a directory
    # that is already on PATH, so the next shell finds it.
    say("")
    runner = os.path.join(target, "run")
    linked = os.access(runner, os.X_OK) and run([runner, "install"]) == 0
    say("")
    if linked:
        say("  installed.  type `lfs` from anywhere")
        say("  first time:  lfs configure     (Enter accepts every default)")
        say("  update:      lfs upgrade")
        say(f"  the folder is {{target}} -- `lfs` runs from there, so keep it")
    else:
        say(f"  installed.  cd {{target}} && ./run")
        say(f"  afterwards:  cd {{target}} && ./run upgrade")
    say("")
    return 0


def main(argv):
    arg = argv[1] if len(argv) > 1 else ""
    if arg in ("-h", "--help"):
        say(__doc__)
        return 0
    if arg == "--check":
        return check()
    if arg == "--uninstall":
        return uninstall()
    if arg not in ("", "--install"):
        say(f"unknown option {{arg}} — try --help")
        return 2
    return install()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--ref", default="unknown")
    parser.add_argument(
        "--out", default="install-lfs.py", help="where to write it; keep it out of the repository"
    )
    args = parser.parse_args()

    blob = collect(ROOT)
    archive = base64.b64encode(blob).decode("ascii")
    text = TEMPLATE.format(
        name=pathlib.Path(args.out).name,
        version=args.version,
        ref=args.ref,
        archive=archive,
    )
    out = pathlib.Path(args.out)
    out.write_text(text, encoding="utf-8")
    out.chmod(0o755)
    print(f"{out}  {out.stat().st_size / 1024:.0f} KB  ({len(blob) / 1024:.0f} KB of source)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
