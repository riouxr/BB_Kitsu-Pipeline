"""Point an existing Maya at this checkout, on any platform.

Maya reads module files out of ``<maya app dir>/<version>/modules``, and one
line in one is enough: it puts ``maya/`` on MAYA_SCRIPT_PATH, which both makes
``BB_pipeline_maya`` importable and makes Maya run the ``userSetup.py``
sitting beside it.

A module file rather than editing a userSetup.py the way the Nuke installer
edits menu.py, because Maya runs *every* userSetup.py it finds - so ours can
simply be its own file, and nothing anybody else owns has to be touched.

    "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" maya/install.py

Runs under any Python 3, not only mayapy - it writes a text file and touches
nothing Maya-specific.
"""

import argparse
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO / "maya"
MODULE_FILE = "BB_pipeline.mod"

VERSION_DIR = re.compile(r"^\d{4}$")

# Maya 2025 is where PySide6 and Python 3.11 arrive. Installing into an
# older one would put a userSetup.py in front of an interpreter that
# cannot import the package, so every launch would open on a traceback.
# Named rather than discovered because the answer is a fact about Maya,
# not about this machine.
MINIMUM = 2025


def _version():
    """The version the package declares, so the .mod does not drift from it."""
    text = (PACKAGE_DIR / "BB_pipeline_maya" / "__init__.py").read_text(
        encoding="utf-8")
    found = re.search(r"^__version__ = '([^']+)'", text, re.M)
    return found.group(1) if found else "0.0.0"


def app_dir():
    """Maya's per-user application directory, wherever this is running.

    MAYA_APP_DIR wins when it is set, because a studio that redirects it has
    done so precisely so that tools stop writing to Documents.
    """
    override = os.environ.get("MAYA_APP_DIR")
    if override:
        return Path(override)

    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Preferences" / "Autodesk" / "maya"
    if sys.platform.startswith("win"):
        return home / "Documents" / "maya"
    return home / "maya"


def versions(root):
    """The Maya version folders that exist, newest first."""
    if not root.is_dir():
        return []
    found = [child.name for child in root.iterdir()
             if child.is_dir() and VERSION_DIR.match(child.name)
             and int(child.name) >= MINIMUM]
    return sorted(found, reverse=True)


def body():
    # Forward slashes even on Windows: Maya reads a backslash in a module
    # file as an escape and the module then silently never loads.
    where = str(PACKAGE_DIR).replace(chr(92), "/")
    return "+ BB_pipeline %s %s\nscripts: .\n" % (_version(), where)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya-version", dest="version",
                        help="only this version, e.g. 2026")
    parser.add_argument("--remove", action="store_true",
                        help="take the module file back out")
    arguments = parser.parse_args()

    root = app_dir()
    wanted = [arguments.version] if arguments.version else versions(root)

    if not wanted:
        print("No Maya %d or newer under %s - run Maya once first"
              % (MINIMUM, root))
        return 1

    too_old = [v for v in wanted if VERSION_DIR.match(v) and int(v) < MINIMUM]
    if too_old and not arguments.remove:
        print("Maya %s predates PySide6 and Python 3.11 - refusing"
              % ", ".join(too_old))
        wanted = [v for v in wanted if v not in too_old]

    written = 0
    for version in wanted:
        modules = root / version / "modules"
        target = modules / MODULE_FILE

        if arguments.remove:
            if target.is_file():
                target.unlink()
                print("Maya %s: removed %s" % (version, target))
            else:
                print("Maya %s: nothing to remove" % version)
            continue

        modules.mkdir(parents=True, exist_ok=True)
        target.write_text(body(), encoding="utf-8")
        written += 1
        print("Maya %s: module -> %s" % (version, PACKAGE_DIR))

    if written:
        print()
        print("Restart Maya. The Kitsu menu appears in the main menu bar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
