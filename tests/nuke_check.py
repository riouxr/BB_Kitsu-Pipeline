"""Nuke-side integration test, with the nuke module stubbed.

Nuke terminal mode needs a render licence, and a workstation without one
cannot run `nuke -t` at all. But the interesting half of the integration is
not Nuke - it is the shot and task filtering, the naming, the versioning and
the Kitsu calls - and all of that can be exercised against a stub.

Run it with Nuke's *own* interpreter, so what is being proved is that the
pipeline works in the Python that Nuke actually ships:

    "C:/Program Files/Nuke16.0v6/python.exe" tests/nuke_check.py

What this does not cover is anything that talks to real Nuke: opening a
script, executing a Write node, the Qt panel. Those need a licensed Nuke.
"""

import json
import os
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "nuke"))

failures = []


def check(condition, description):
    print(("  ok   " if condition else "  FAIL ") + description)
    if not condition:
        failures.append(description)


# -- a nuke module good enough to exercise the pipeline ----------------------

class StubKnob:
    def __init__(self, name, label=""):
        self._name = name
        self._value = ""
        self.flags = []

    def name(self):
        return self._name

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = value

    def setFlag(self, flag):
        self.flags.append(flag)


class StubRoot:
    def __init__(self):
        self.knobs = {"first_frame": StubKnob("first_frame"),
                      "last_frame": StubKnob("last_frame"),
                      "fps": StubKnob("fps")}
        self._name = "Root"

    def knob(self, name):
        return self.knobs.get(name)

    def addKnob(self, knob):
        self.knobs[knob.name()] = knob

    def name(self):
        return self._name


def make_stub():
    stub = types.ModuleType("nuke")
    stub.INVISIBLE = 0x00040000
    stub.NUKE_VERSION_MAJOR = 16
    stub._root = StubRoot()
    stub.saved = []
    stub.opened = []
    stub.messages = []
    stub.answer = True

    stub.root = lambda: stub._root
    stub.String_Knob = StubKnob
    stub.modified = lambda: False
    stub.ask = lambda text: stub.answer
    stub.message = lambda text: stub.messages.append(text)
    stub.tprint = lambda *a: None
    stub.scriptClear = lambda: stub._root.knobs.pop("BB_pipeline", None)

    def script_save_as(path, overwrite=0):
        stub.saved.append(path)
        stub._root._name = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("# stub nuke script\n", encoding="utf-8")

    def script_open(path):
        stub.opened.append(path)
        stub._root._name = path

    stub.scriptSaveAs = script_save_as
    stub.scriptOpen = script_open
    return stub


BRIEF_WITH_ROOT = """Notes about the show.

[bb]
work_root = "Q:/FromTheBrief"
"""

sys.modules["nuke"] = make_stub()
import nuke  # noqa: E402


def main():
    work_root = Path(tempfile.mkdtemp(prefix="kitsu_nuke_"))
    os.environ["BB_PIPELINE_SETTINGS"] = str(work_root / "settings.json")

    from BB_core import settings
    settings.save({"work_root": str(work_root), "render_root": str(work_root),
                   "publish_on_save": True})

    import BB_pipeline_nuke as package
    from BB_pipeline_nuke import fetch, publish, scripts, session, stamp

    check(package.core.available,
          "shared core located (%s)" % (package.core.error or "ok"))
    check(sys.version_info[:2] == (3, 11),
          "running under Nuke's Python (%s)" % sys.version.split()[0])
    from BB_core import transport
    check(transport.make_transport().name == "urllib",
          "and therefore on the urllib transport (%s)"
          % transport.make_transport().name)

    state = session.state

    # -- shots only, compositing only ----------------------------------------
    check(session.ENTITY_TYPE == "shot", "Nuke browses shots, not assets")
    check(settings.config().dcc("nuke")["ext"] == "nk", "and writes .nk files")

    LIGHTING = {"id": "d1", "name": "Lighting"}
    COMP = {"id": "d2", "name": "Compositing"}
    state.departments = {d["id"]: d for d in (LIGHTING, COMP)}
    state.task_types = {
        "tt1": {"id": "tt1", "name": "Lighting", "department_id": "d1"},
        "tt2": {"id": "tt2", "name": "Compositing", "department_id": "d2"},
    }
    state.tasks = [{"id": "t1", "task_type_id": "tt1"},
                   {"id": "t2", "task_type_id": "tt2"}]
    state.task_departments = session.departments_for_nuke()

    check(state.task_departments and "compositing" in state.task_departments,
          "the department filter comes from the config (%s)"
          % sorted(state.task_departments or []))
    offered = [state.task_type_name(t["task_type_id"]) for t in state.comp_tasks()]
    check(offered == ["Compositing"],
          "only compositing tasks are offered (%s)" % offered)

    # -- context, naming and paths -------------------------------------------
    state.projects = [{"id": "p1", "name": "PizzaHunt", "code": None, "fps": "24"}]
    state.sequences = [{"id": "s1", "name": "sc01", "parent_id": "p1"}]
    state.shots = [{"id": "sh1", "name": "sh01", "parent_id": "s1",
                    "nb_frames": 252,
                    "data": {"frame_in": 1001, "frame_out": 1252}}]

    context = fetch.current_context("p1", "s1", "sh1", "t2")
    check(context is not None, "context builds from a shot selection")
    check(context.entity_type == "shot", "and knows it is a shot")
    check(context.versioned(1) == "PizzaHunt_sc01_sh01_Compositing_v001",
          "named to the studio scheme (%s)" % context.versioned(1))
    check(context.task == "Compositing", "with the compositing task")

    from BB_core import workfiles
    path = workfiles.work_file(context, "nuke", 1, settings.config())
    check(str(path).endswith(".nk"), "the work file is a .nk (%s)" % path.name)
    check("sc01" in path.parts and "sh01" in path.parts,
          "under the shot, in the shared layout")

    # -- the project's own settings have to be read --------------------------
    # A root set in the Kitsu brief is only found if the project is handed to
    # the config. Forgetting that is how "Set a Work Root" appears for a
    # project that plainly has one.
    state.projects = [{
        "id": "p1", "name": "PizzaHunt", "code": None, "fps": "24",
        "description": BRIEF_WITH_ROOT,
    }]
    bare = settings.config()
    with_project = session.config_for(project_id="p1")
    check(bare.paths.get("work_root") == str(work_root),
          "without the project, only the settings file is read")
    check(with_project.paths.get("work_root") == "Q:/FromTheBrief",
          "with it, the brief wins (%s)" % with_project.paths.get("work_root"))

    from BB_core import filetree
    state.projects[0]["file_tree"] = {"working": {"folder_path": {
        "shot": "<Sequence>/<Shot>/<TaskType>"}}}
    tree_config = session.config_for(project_id="p1")
    check(tree_config.paths.get("work_dir_shot") == "{group}/{entity}/{task}",
          "and a file_tree on the project is read too")

    # Put the plain project back for the rest of the checks.
    state.projects = [{"id": "p1", "name": "PizzaHunt", "code": None, "fps": "24"}]

    # -- stamping on the root node -------------------------------------------
    stamp.write(context.at_version(3))
    knob = nuke.root().knob("BB_pipeline")
    check(knob is not None, "the context is stamped onto a root knob")
    check(nuke.INVISIBLE in knob.flags, "and the knob is hidden")
    stored = json.loads(knob.value())
    check(stored["entity_id"] == "sh1" and stored["task_id"] == "t2",
          "the Kitsu ids go with it")

    recovered, source = stamp.read_current()
    check(source == "stamp" and recovered.version == 3,
          "and read back (%s v%s)" % (source, recovered.version))

    # -- creating and versioning ---------------------------------------------
    created = scripts.create_version(context)
    check(created and Path(created).is_file(),
          "create_version writes a script (%s)" % os.path.basename(created or ""))
    check(os.path.basename(created) == "PizzaHunt_sc01_sh01_Compositing_v001.nk",
          "starting at v001")
    check(nuke.root().knob("first_frame").value() == 1001
          and nuke.root().knob("last_frame").value() == 1252,
          "the Kitsu frame range is applied (%s-%s)"
          % (nuke.root().knob("first_frame").value(),
             nuke.root().knob("last_frame").value()))
    check(nuke.root().knob("fps").value() == 24.0, "and the project frame rate")

    found = fetch.list_versions(context)
    check(len(found) == 1, "the version is found on disk (%d)" % len(found))

    bumped = scripts.save_next_version()
    check(os.path.basename(bumped) == "PizzaHunt_sc01_sh01_Compositing_v002.nk",
          "save_next_version increments (%s)" % os.path.basename(bumped))
    check(stamp.read().version == 2, "and the stamp follows the version")

    # A foreign script in the folder must not move the version on.
    (Path(created).parent / "old_comp_backup.nk").write_text("", encoding="utf-8")
    (Path(created).parent / "PizzaHunt_sc01_sh01_Lighting_v009.nk").write_text(
        "", encoding="utf-8")
    check(len(fetch.list_versions(context)) == 2,
          "scripts from another task are ignored")

    # -- publishing is gated the same way as Blender's -----------------------
    state.client = None
    check("not connected" in publish.why_not(context),
          "no publish without a Kitsu session")

    class StubClient:
        logged_in = True
        host = "http://kitsu.test"
        posted = []

        def task(self, task_id):
            return {"task_status_id": "st1"}

        def _request(self, method, path, **kwargs):
            StubClient.posted.append((path, kwargs.get("json", {})))
            return {"id": "c1"}

    state.client = StubClient()
    check(publish.why_not(context) == "", "a stamped, connected context can publish")

    note = publish.send(context, created, comment="looks good",
                        task_status_id="st9")
    check("updated" in note.lower(), "publishing reports success (%r)" % note)
    posted = StubClient.posted[-1][1] if StubClient.posted else {}
    check(posted.get("comment") == "looks good", "the comment is sent")
    check(posted.get("task_status_id") == "st9", "with the chosen status")

    idless = context.__class__(entity_type="shot", project="X", group="a",
                               entity="b", task="c", version=1)
    check("no Kitsu task" in publish.why_not(idless),
          "a context with no ids has nowhere to publish")

    settings.save({"publish_on_save": False})
    check("switched off" in publish.why_not(context),
          "and the preference is honoured")
    settings.save({"publish_on_save": True})

    # -- importing into the open script --------------------------------------
    nuke.pasted = []
    nuke.nodePaste = lambda path: nuke.pasted.append(path)

    imported = scripts.import_into_current(created)
    check(nuke.pasted == [created],
          "import pastes the version into the open script (%s)" % nuke.pasted)
    check(imported == created, "and reports what it imported")
    check(nuke.opened == [], "without opening it, which would replace the comp")

    try:
        scripts.import_into_current(str(Path(created).parent / "gone.nk"))
        refused = False
    except scripts.ScriptError:
        refused = True
    check(refused, "a missing file is refused rather than pasted")

    # -- Kitsu thumbnails ------------------------------------------------------
    from BB_pipeline_nuke import thumbnails
    thumbnails.clear()

    class ThumbClient:
        calls = []

        def thumbnail(self, preview_file_id):
            ThumbClient.calls.append(preview_file_id)
            return b"stub thumbnail bytes"



    client = ThumbClient()
    with_thumb = {"id": "sh1", "preview_file_id": "pf1"}
    without = {"id": "sh2", "preview_file_id": None}

    check(thumbnails.fetch(client, with_thumb) is not None,
          "a shot thumbnail is downloaded")
    ThumbClient.calls = []
    thumbnails.fetch(client, with_thumb)
    check(ThumbClient.calls == [],
          "a second look costs no request - preview files are immutable")
    check(thumbnails.fetch(client, without) is None
          and ThumbClient.calls == [],
          "a shot with no preview is never asked for")
    thumbnails.clear()
    check(not thumbnails._cache, "the cache empties on clear")

    # -- the menu -------------------------------------------------------------
    check(package.MENU == "Kitsu", "the menu is called Kitsu")
    for name in ("open_browser", "save_next_version", "update_kitsu",
                 "open_settings"):
        check(callable(getattr(package, name, None)),
              "%s is reachable from the menu" % name)

    import shutil
    shutil.rmtree(work_root, ignore_errors=True)

    print()
    if failures:
        print("%d FAILED:" % len(failures))
        for description in failures:
            print("  - %s" % description)
        sys.exit(1)
    print("all Nuke checks passed")


if __name__ == "__main__":
    main()
