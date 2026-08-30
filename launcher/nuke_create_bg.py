"""Run *inside* Nuke (``nuke -t nuke_create_bg.py -- <json>``) to create a
task's first script when Launch finds none.

Mirrors ``blender_create_bg.py`` - see that file for why calling straight
into the add-on's own creation function, rather than re-deriving stamping/
frame-rate-sync/thumbnail-attempt out here a second time, is the right
approach and not a shortcut. ``BB_pipeline_nuke.scripts.create_version``
handles a script with no GUI already: a freshly-started Nuke's root is
unmodified, so the "discard unsaved changes?" confirmation it would
otherwise need to show never triggers, and its thumbnail attempt already
reports "nothing to snapshot" instead of raising when there is no viewer or
selection to grab from - both exactly this script's situation.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

argv = sys.argv[sys.argv.index("--") + 1:]
payload = json.loads(argv[0])

from BB_core.context import EntityContext
from BB_pipeline_nuke import scripts

shot_context = EntityContext(**payload["context"])

created = scripts.create_version(shot_context, from_current=False)

if not created:
    print("BB_LAUNCH_CREATE_FAILED")
    sys.exit(1)

print("BB_LAUNCH_CREATE_OK")
