"""Package the Blender add-on as an installable extension zip.

The add-on and the shared core are separate directories in the repo - the core
is shared with Nuke, Resolve and the standalone browser, so it cannot live
inside the Blender add-on - but an extension zip has to be self-contained.
This stages a copy with BB_core folded in, and lets Blender build the zip so
the manifest is validated the same way the extensions platform would.

    python tools/build_extension.py
    python tools/build_extension.py --blender "C:/Program Files/.../blender.exe"
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADDON = REPO / "blender" / "BB_pipeline"
CORE = REPO / "BB_core"
DIST = REPO / "dist"

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.zip", ".*")

WINDOWS_BLENDERS = Path("C:/Program Files/Blender Foundation")


def _version_key(path):
    """Sort "Blender 4.5" below "Blender 5.1" numerically, not as text."""
    digits = path.parent.name.split()[-1].split(".")
    return tuple(int(part) if part.isdigit() else 0 for part in digits)


def find_blender(explicit=None):
    if explicit:
        return explicit

    for name in ("BLENDER", "BLENDER_EXE"):
        if os.environ.get(name):
            return os.environ[name]

    if WINDOWS_BLENDERS.is_dir():
        # Newest installed wins; the manifest's blender_version_min is what
        # decides compatibility, not what happened to build the zip.
        found = sorted(WINDOWS_BLENDERS.glob("Blender */blender.exe"), key=_version_key)
        if found:
            return str(found[-1])

    return shutil.which("blender")


def stage(target):
    shutil.copytree(ADDON, target, ignore=IGNORE)
    shutil.copytree(CORE, target / "BB_core", ignore=IGNORE)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", help="path to blender.exe")
    parser.add_argument("--output", default=str(DIST), help="where to write the zip")
    args = parser.parse_args()

    blender = find_blender(args.blender)
    if not blender:
        sys.exit("Could not find Blender - pass --blender or set $BLENDER")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        source = stage(Path(tmp) / "BB_pipeline")
        result = subprocess.run(
            [blender, "--command", "extension", "build",
             "--source-dir", str(source),
             "--output-dir", str(output)],
            capture_output=True, text=True,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode != 0:
            sys.exit(result.returncode)

    for zipped in sorted(output.glob("BB_pipeline-*.zip")):
        print("built %s (%d KB)" % (zipped, zipped.stat().st_size // 1024))


if __name__ == "__main__":
    main()
