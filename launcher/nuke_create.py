"""Creates a task's first Nuke script when none exists yet - by launching
Nuke itself with ``nuke_create_bg.py``, which stays open afterward.

Deliberately not the same shape as ``blender_create.py``. Blender's creation
step runs in a throwaway ``--background`` process that exits the moment it
is done, so waiting for it and reading its output back is exactly right.
Nuke has no such disposable mode here: ``-t`` (terminal) needs a license
type this machine has none configured for, and the interactive session that
does work is the artist's actual working window - it is not supposed to
exit, ever, until the artist closes it. Waiting for it to finish and then
reading its buffered stdout (the first version of this tried exactly that)
never returns: an interactive Nuke's stdout is fully buffered rather than
line-buffered when redirected, so nothing written to it is even visible
until the process exits - which, correctly, it does not.

So this fires the launch and returns immediately, same as ``bb_launch.
launch()`` already does for a script that already exists - Launch has never
waited to confirm Blender or Nuke actually finished opening a file, and
creating one is no different.
"""
import json
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().with_name("nuke_create_bg.py")


def create(exe, context):
    """Launches Nuke on this context's next version, creating it on the way.

    Raises RuntimeError only for what is knowable up front - no exe
    configured. Whether the script inside Nuke actually succeeds is not
    watched for, the same way opening an existing file is not watched for
    either; see the module docstring for why waiting is the wrong idea here.
    """
    if not exe:
        raise RuntimeError("no nuke executable configured")

    payload = json.dumps({"context": context.to_dict()})
    subprocess.Popen([exe, str(_SCRIPT), "--", payload])
