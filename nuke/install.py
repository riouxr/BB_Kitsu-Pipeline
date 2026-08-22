"""Add the Kitsu menu to an existing ~/.nuke setup.

Nuke loads ``~/.nuke/menu.py`` at startup, and that file usually already
belongs to something else - on this machine, Prism. So rather than replacing
it, this appends a block delimited exactly the way Prism delimits its own:

    # >>>BBPipelineStart
    ...
    # <<<BBPipelineEnd

Running it again replaces that block and leaves everything around it alone.
``--remove`` takes it out again.

    "C:/Program Files/Nuke16.0v6/python.exe" nuke/install.py
"""

import argparse
import os
import sys
from pathlib import Path

START = "# >>>BBPipelineStart"
END = "# <<<BBPipelineEnd"

REPO = Path(__file__).resolve().parent.parent


def block(package_dir):
    return """%s
# BB Kitsu Pipeline - Kitsu integration for Nuke.
# Managed by nuke/install.py; edit the repository, not this block.
import sys

_BB_pipeline = r"%s"
if _BB_pipeline not in sys.path:
    sys.path.append(_BB_pipeline)

try:
    import BB_pipeline_nuke
    BB_pipeline_nuke.install_menu()
except Exception as _error:
    import nuke
    nuke.tprint("BB Kitsu Pipeline failed to load: %%s" %% _error)
%s
""" % (START, package_dir, END)


def strip_existing(text):
    """The file without our block, however many times it appears."""
    while START in text and END in text:
        head, rest = text.split(START, 1)
        _ours, tail = rest.split(END, 1)
        text = head.rstrip("\n") + "\n" + tail.lstrip("\n")
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nuke-dir", default=str(Path.home() / ".nuke"),
                        help="the .nuke folder to install into")
    parser.add_argument("--remove", action="store_true",
                        help="take the block out again")
    arguments = parser.parse_args()

    folder = Path(arguments.nuke_dir)
    folder.mkdir(parents=True, exist_ok=True)
    menu = folder / "menu.py"

    existing = menu.read_text(encoding="utf-8") if menu.is_file() else ""
    had_ours = START in existing
    cleaned = strip_existing(existing)

    if arguments.remove:
        menu.write_text(cleaned, encoding="utf-8")
        print("removed" if had_ours else "nothing to remove")
        return

    package_dir = str(REPO / "nuke")
    updated = cleaned.rstrip("\n")
    if updated:
        updated += "\n\n"
    updated += block(package_dir)

    menu.write_text(updated, encoding="utf-8")
    print("%s %s" % ("updated" if had_ours else "installed", menu))
    print("pointing at %s" % package_dir)

    other = [line for line in cleaned.splitlines() if line.startswith("# >>>")]
    if other:
        print("left alone: %s" % ", ".join(other))


if __name__ == "__main__":
    main()
