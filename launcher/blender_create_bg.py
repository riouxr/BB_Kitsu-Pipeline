"""Run *inside* Blender (``--background --python blender_create_bg.py --
<json>``) to create a task's first work file when Launch finds none.

Reuses the add-on's own creation logic (``operators._create_version``)
rather than re-implementing stamping/frame-range-sync/thumbnail-attempt a
second time from outside Blender - that function already guards every
UI-only step (viewport thumbnail capture, the "publish now?" popup) behind
``bpy.app.background``/window checks, so it is already safe to call this
way; nothing here had to be written to make that true.

Bypasses the operator (``bpy.ops.bb.new_workfile``) entirely: that operator
reads its ``EntityContext`` off the browser's own selected project/sequence/
shot/task properties, which only exist because a person clicked through the
browser UI. Running headless, with no browser selection to read, the
context comes straight from what ``bb_launch.py`` already resolved via
Kitsu - passed in as JSON after ``--`` - so ``_create_version`` is called
directly with it instead.
"""
import json
import sys
from pathlib import Path

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
payload = json.loads(argv[0])

# The add-on's extension repo id is "user_default" on every install seen so
# far (including the dev-symlinked ones - the symlink still sits under that
# same repo folder), but is not part of any documented contract, so this
# does not assume it: it finds whichever module actually registered as
# BB_pipeline instead of hardcoding the path.
_addon_name = next(
    name for name in sys.modules
    if name == "BB_pipeline" or name.endswith(".BB_pipeline"))

context_module = sys.modules.get(_addon_name + ".BB_core.context")
if context_module is None:
    import importlib
    context_module = importlib.import_module(_addon_name + ".BB_core.context")
operators = sys.modules.get(_addon_name + ".operators")
if operators is None:
    import importlib
    operators = importlib.import_module(_addon_name + ".operators")

EntityContext = context_module.EntityContext

shot_context = EntityContext(**payload["context"])
path = Path(payload["path"])
path.parent.mkdir(parents=True, exist_ok=True)

operators._create_version(shot_context, path, from_current=False)

if not path.is_file():
    # _create_version reports failures through bpy's own operator/status
    # system, which nothing is listening to here - so the one signal this
    # script can actually give its caller back is whether the file exists
    # afterward.
    print("BB_LAUNCH_CREATE_FAILED")
    sys.exit(1)

print("BB_LAUNCH_CREATE_OK")
