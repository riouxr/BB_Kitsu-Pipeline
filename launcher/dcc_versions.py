"""Switch which installed Blender/Nuke build the launcher opens scenes with.

The launcher itself always opens the *latest saved version* of the scene -
that part is not a choice. This is about the other version: which build of
the DCC does the opening, out of however many are installed side by side.

    python dcc_versions.py list              # every Blender/Nuke found on this machine
    python dcc_versions.py list blender      # just one dcc
    python dcc_versions.py set blender 5.1   # match by a substring of the path
    python dcc_versions.py current           # what bb_launch.py will use right now

Search paths are Windows-only and cover where these two install by default;
nothing here touches the registry or asks Windows what is installed, so an
install in a nonstandard location has to be set with an explicit full path
instead of a version substring.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import launcher_config

_SEARCH = {
    "blender": [
        (Path(r"C:\Program Files\Blender Foundation"), "*/blender.exe"),
    ],
    "nuke": [
        (Path(r"C:\Program Files"), "Nuke*/Nuke*.exe"),
    ],
}

_SETTING = {"blender": "blender_exe", "nuke": "nuke_exe"}


def found(dcc):
    """Every install of *dcc* on this machine, sorted.

    Nuke's install folder also ships a crash-reporter exe that matches the
    same glob the real one does, so anything with "crash" in its name is
    dropped rather than offered as a launchable build.
    """
    results = []
    for root, pattern in _SEARCH.get(dcc, []):
        if root.is_dir():
            results.extend(root.glob(pattern))
    results = [p for p in results if "crash" not in p.name.lower()]
    return sorted(set(results), key=lambda p: str(p))


def _dccs(argv):
    return [argv[0]] if argv else list(_SEARCH)


def cmd_list(argv):
    for dcc in _dccs(argv):
        installs = found(dcc)
        current = launcher_config.get(_SETTING[dcc], "")
        print("%s:" % dcc)
        if not installs:
            print("  (none found under the default install location)")
        for path in installs:
            mark = " <- current" if str(path) == current else ""
            print("  %s%s" % (path, mark))


def cmd_current(argv):
    for dcc in _dccs(argv):
        print("%s: %s" % (dcc, launcher_config.get(_SETTING[dcc]) or "(not set)"))


def cmd_set(argv):
    if len(argv) < 2:
        print("usage: dcc_versions.py set <blender|nuke> <version substring or full path>")
        return 1
    dcc, wanted = argv[0], argv[1]
    if dcc not in _SETTING:
        print("unknown dcc %r - choose from: %s" % (dcc, ", ".join(_SETTING)))
        return 1

    candidate = Path(wanted)
    if candidate.is_file():
        chosen = candidate
    else:
        matches = [p for p in found(dcc) if wanted.lower() in str(p).lower()]
        if not matches:
            print("no %s install matching %r - try: python dcc_versions.py list %s"
                  % (dcc, wanted, dcc))
            return 1
        if len(matches) > 1:
            print("more than one match for %r:" % wanted)
            for path in matches:
                print("  %s" % path)
            return 1
        chosen = matches[0]

    launcher_config.set(_SETTING[dcc], str(chosen))
    print("%s -> %s" % (dcc, chosen))
    return 0


COMMANDS = {"list": cmd_list, "current": cmd_current, "set": cmd_set}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 1
    return COMMANDS[argv[0]](argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
