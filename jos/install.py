"""Install the Kitsu menu into JOS Compositor.

JOS loads every plugin folder in its user plugin folder at startup
(%APPDATA%/Jungle of Sharks/JOS Compositor/plugins on Windows). This writes a
small loader plugin there, ``BB_Kitsu/menu.py``, pointing at this
repository - so the repository stays the one copy, as with the Nuke install.
Running it again rewrites the loader; ``--remove`` deletes it.

    python jos/install.py

A studio can instead set JOS_PLUGIN_PATH to this ``jos`` folder.
"""

import argparse
import os
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAME = "BB_Kitsu"


def user_plugins():
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Jungle of Sharks" / "JOS Compositor" / "plugins"


def loader(package_dir):
    return '''# BB Kitsu Pipeline - Kitsu integration for JOS Compositor.
# Written by jos/install.py; edit the repository, not this file.
import sys

_BB_pipeline = r"%s"
if _BB_pipeline not in sys.path:
    sys.path.insert(0, _BB_pipeline)

try:
    import BB_pipeline_jos
    BB_pipeline_jos.install_menu()
except Exception as _error:
    print("BB Kitsu Pipeline failed to load: %%s" %% _error)
''' % package_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugins", default=str(user_plugins()),
                        help="the JOS plugin folder to install into")
    parser.add_argument("--remove", action="store_true",
                        help="delete the loader again")
    arguments = parser.parse_args()

    folder = Path(arguments.plugins) / NAME
    if arguments.remove:
        if folder.is_dir():
            shutil.rmtree(folder)
            print("removed %s" % folder)
        else:
            print("nothing to remove")
        return

    folder.mkdir(parents=True, exist_ok=True)
    package_dir = str(REPO / "jos")
    (folder / "menu.py").write_text(loader(package_dir), encoding="utf-8")
    print("installed %s" % (folder / "menu.py"))
    print("pointing at %s" % package_dir)


if __name__ == "__main__":
    main()
