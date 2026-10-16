"""Run *inside* jos-python (``jos-python --no-plugins jos_create_bg.py <json>``)
to create a compositing task's first JOS project when Launch finds none.

Mirrors ``nuke_create_bg.py``: it calls the plugin's own Create
(``BB_pipeline_jos.scripts.create_version``: the stamp, Kitsu's frame range
and fps, the save) rather than rebuilding it here, then adds a Kitsu Write as
an artist's first step would, and saves again. ``--no-plugins`` so an
installed copy of the plugin is not loaded beside this checkout's.

Unlike Nuke, JOS has a headless interpreter, so this works the way Blender's
does: a throwaway process that exits when the file is written, after which
Launch opens the file in JOS like any other version.

The payload carries what a browser session would have cached and a cold
start has not: the shot (for its frame range) and the project (for its fps
and the roots in its brief).
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "jos"))

payload = json.loads(sys.argv[1])

from BB_core.context import EntityContext  # noqa: E402
from BB_pipeline_jos import scripts, session, writenode  # noqa: E402

state = session.state
if payload.get("project"):
    state.projects = [payload["project"]]
if payload.get("shot"):
    state.shots = [payload["shot"]]

context = EntityContext(**payload["context"])

try:
    created = scripts.create_version(context, from_current=False)
except scripts.ScriptError as error:
    print("BB_LAUNCH_CREATE_FAILED %s" % error)
    sys.exit(1)

if not created:
    print("BB_LAUNCH_CREATE_FAILED")
    sys.exit(1)

# The Kitsu Write, pointed at v001's render folder. A project with no render
# root still gets its v001: the Write can be made later from the Kitsu menu.
try:
    node = writenode.create()
    import jos
    jos.save()
    print("[BB] %s -> %s" % (node.name, node["file"]))
except writenode.WriteError as error:
    print("[BB] no Kitsu Write: %s" % error)

print("BB_LAUNCH_CREATE_OK %s" % created)
