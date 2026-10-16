"""Where another application's executable is, to open a file it owns.

Nuke cannot open a JOS project, so its browser offers to start JOS
Compositor on it instead; the launcher needs the same answer. The launcher's
own settings file (``launcher/launcher_config.py`` writes it) is read first,
then the place the application installs to by default.

Read only: this module never writes the launcher's file.
"""

import json
import os
import subprocess
from pathlib import Path

LAUNCHER_FILE = Path.home() / ".BB_pipeline" / "launcher.json"

# Where an application installs when nobody chose otherwise. Only the ones
# with a single, unversioned install folder: Blender and Nuke put the version
# in the path, which is what ``launcher/dcc_versions.py`` is for.
DEFAULT_PATHS = {
    "jos": [r"C:\Program Files\Jungle of Sharks\JOS Compositor\JOSCompositor.exe"],
}

# An environment variable wins over everything, for a studio deploying the
# application somewhere else on every machine.
ENV = {"jos": "JOS_COMPOSITOR_EXE"}

NAMES = {"jos": "JOS Compositor", "nuke": "Nuke", "blender": "Blender",
         "maya": "Maya", "resolve": "DaVinci Resolve"}


def name(dcc):
    """What the application is called, for a label or a message."""
    return NAMES.get(dcc, dcc)


def _configured(dcc):
    try:
        with open(LAUNCHER_FILE, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return ""
    if not isinstance(stored, dict):
        return ""
    return str(stored.get("%s_exe" % dcc) or "")


def executable(dcc):
    """The executable to start *dcc* with, or '' when none is found."""
    candidates = [os.environ.get(ENV.get(dcc, ""), "") if dcc in ENV else "",
                  _configured(dcc)] + DEFAULT_PATHS.get(dcc, [])
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def open_with(dcc, path):
    """Start *dcc* on *path*, detached. Returns the executable, or raises.

    RuntimeError carries a message meant for the artist.
    """
    exe = executable(dcc)
    if not exe:
        raise RuntimeError(
            "Cannot find %s on this machine - set it with "
            "python launcher/dcc_versions.py set %s <path>" % (name(dcc), dcc))
    try:
        subprocess.Popen([exe, str(path)], close_fds=True)
    except OSError as error:
        raise RuntimeError("Could not start %s: %s" % (name(dcc), error))
    return exe
