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
import pathlib
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

    def evaluate(self):
        return self._value


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

    # Enough of the node API for a snapshot: a Write built with nuke.nodes,
    # executed for one frame, then deleted.
    stub.selected = []
    stub.viewer_input = None
    stub.created = []
    stub.deleted = []
    stub.executed = []
    stub.frame = lambda: 1001

    class StubNode:
        def __init__(self, name="Node1", node_class=None, **knobs):
            self._name = name
            self._class = node_class or "".join(
                c for c in name if not c.isdigit()) or "Node"
            self.knobs = dict(knobs)
            self._objects = {}

        def name(self):
            return self._name

        def Class(self):
            return self._class

        def setName(self, name, uncollide=False):
            self._name = name

        def input(self, index):
            return stub.viewer_input

        def knob(self, name):
            if name in self._objects:
                return self._objects[name]
            if name in self.knobs:
                k = StubKnob(name)
                k.setValue(self.knobs[name])
                self._objects[name] = k
                return k
            return None

        def addKnob(self, knob):
            self._objects[knob.name()] = knob

        def value_of(self, name):
            knob = self.knob(name)
            return knob.value() if knob else None

        def xpos(self):
            return 0

        def ypos(self):
            return 0

        def setXYpos(self, x, y):
            pass

    class Nodes:
        @staticmethod
        def Write(**knobs):
            node = StubNode("Write1", node_class="Write", **knobs)
            stub.created.append(node)
            return node

        @staticmethod
        def Read(**knobs):
            node = StubNode("Read1", node_class="Read", **knobs)
            stub.created.append(node)
            return node

    def execute(node, first, last):
        stub.executed.append((node, first, last))
        path = node.knobs.get("file", "")
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"rendered png bytes")

    class Viewer:
        @staticmethod
        def node():
            return StubNode("Viewer1")

        @staticmethod
        def activeInput():
            return 0

    def all_nodes(kind=None):
        # Deleted nodes are gone; the pipeline creates temporaries and
        # removes them, and a repoint must not chase those.
        alive = [node for node in stub.created if node not in stub.deleted]
        if kind is None:
            return alive
        return [node for node in alive if node.Class() == kind]

    stub.nodes = Nodes()
    stub.allNodes = all_nodes
    stub.selectedNodes = lambda: stub.selected
    stub.activeViewer = lambda: Viewer() if stub.viewer_input else None
    stub.execute = execute
    stub.delete = lambda node: stub.deleted.append(node)
    stub.StubNode = StubNode

    class TabKnob(StubKnob):
        pass

    class StubMenu:
        def __init__(self, name="root"):
            self._name = name
            self.commands = []
            self.menus = {}

        def addMenu(self, name, **kwargs):
            self.menus.setdefault(name, StubMenu(name))
            return self.menus[name]

        def addCommand(self, name, command="", shortcut=None):
            self.commands.append((name, command, shortcut))

        def addSeparator(self):
            pass

        def items(self):
            return []

    stub.menus = {}

    def menu(which):
        stub.menus.setdefault(which, StubMenu(which))
        return stub.menus[which]

    stub.menu = menu
    stub.Tab_Knob = TabKnob
    stub.Text_Knob = StubKnob
    stub.PyScript_Knob = lambda name, label="", command="": StubKnob(name, label)
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
          "only compositing tasks are Nuke's to author (%s)" % offered)

    # Browsing is not authoring. Filtering the tree down to compositing hid
    # the renders a comp is assembled from behind a department the comper
    # does not own - a lighting task has no script to open, but its EXRs are
    # exactly what gets read in.
    browsable = [state.task_type_name(t["task_type_id"])
                 for t in state.browsable_tasks()]
    check(sorted(browsable) == ["Compositing", "Lighting"],
          "every task on the shot is browsable (%s)" % browsable)
    check(browsable[0] == "Compositing",
          "with the ones Nuke authors first (%s)" % browsable)

    lighting = next(t for t in state.tasks if t["task_type_id"] == "tt1")
    compositing = next(t for t in state.tasks if t["task_type_id"] == "tt2")
    check(state.authors(compositing) and not state.authors(lighting),
          "and only compositing counts as authored here")

    # -- context, naming and paths -------------------------------------------
    state.projects = [{"id": "p1", "name": "PizzaHunt", "code": None, "fps": "24"}]
    state.sequences = [{"id": "s1", "name": "sc01", "parent_id": "p1"}]
    state.shots = [{"id": "sh1", "name": "sh01", "parent_id": "s1",
                    "nb_frames": 252,
                    "data": {"frame_in": 1001, "frame_out": 1252}}]

    context = fetch.current_context("p1", "s1", "sh1", "t2")
    check(context is not None, "context builds from a shot selection")
    check(context.entity_type == "shot", "and knows it is a shot")
    check(context.versioned(1) == "sh01_v001",
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

    # A script with no stamp gets its context from its path, and the path
    # carries no project: the names stopped repeating what the folders say,
    # and the project is the root rather than a folder. Without a fallback
    # the brief is never read and every root looks unset - which is what a
    # second scene opened in the same show reported.
    from BB_core.context import EntityContext as _Ctx

    nameless = _Ctx(group="sc01", entity="sh01", task="Compositing",
                    entity_type="shot", version=1)
    check(not nameless.project_id, "a path-recovered context has no project id")

    settings.save({"last_project": "p1"})
    recovered = session.config_for(nameless)
    check(recovered.paths.get("work_root") == "Q:/FromTheBrief",
          "the browser's project fills the gap (%s)"
          % recovered.paths.get("work_root"))

    # The case that matters most: a Write made from the Nodes menu in a Nuke
    # that never opened the browser. There is no Kitsu session at all, so
    # nothing that needs the server can answer - and the roots live in the
    # project's brief. What the browser last saw is kept for exactly this.
    session.remember_project(state.projects[0])
    kept_client_2 = state.client
    kept_projects_2 = state.projects
    state.client = None
    state.projects = []
    try:
        check(not state.connected, "no Kitsu session, as a fresh Nuke has none")
        offline = session.project_of(_Ctx(group="sc01", entity="sh01",
                                          task="Compositing",
                                          entity_type="shot", version=1))
        check(offline is not None and offline.get("name") == "PizzaHunt",
              "the project is found without one (%s)"
              % (offline or {}).get("name"))
        check(session.config_for(_Ctx(group="sc01", entity="sh01",
                                      task="Compositing", entity_type="shot",
                                      version=1)).paths.get("work_root")
              == "Q:/FromTheBrief",
              "and its brief still supplies the roots")
    finally:
        state.client = kept_client_2
        state.projects = kept_projects_2

    # An id that answers nothing is worth no more than no id. A stamp
    # written against another server, or a project since deleted, otherwise
    # tells a comper who plainly has a project open that there is none.
    stale = _Ctx(group="sc01", entity="sh01", task="Compositing",
                 entity_type="shot", version=1,
                 project_id="00000000-dead-beef-0000-000000000000")
    check(session.project_of(stale) is not None
          and session.project_of(stale)["id"] == "p1",
          "an id that resolves to nothing falls back to the browser's project")
    check(session.config_for(stale).paths.get("work_root") == "Q:/FromTheBrief",
          "and the brief is read after all (%s)"
          % session.config_for(stale).paths.get("work_root"))

    # And when the session never loaded the project list at all - a Write
    # made from the Nodes menu without the browser ever being opened.
    kept_projects = state.projects
    kept_client = state.client

    class OneProjectClient:
        logged_in = True
        host = "http://kitsu.test"
        asked = []

        def project(self, project_id):
            OneProjectClient.asked.append(project_id)
            return {"id": "p1", "name": "PizzaHunt", "code": None,
                    "fps": "24", "description": BRIEF_WITH_ROOT}

    state.projects = []
    state.client = OneProjectClient()
    fetched = session.config_for(nameless)
    check(fetched.paths.get("work_root") == "Q:/FromTheBrief",
          "fetched when the cache is empty (%s)"
          % fetched.paths.get("work_root"))
    check(any(p["id"] == "p1" for p in state.projects),
          "and kept, so the next Write does not ask again")
    session.config_for(nameless)
    check(len(OneProjectClient.asked) == 1,
          "asked for once, not on every Write (%d)" % len(OneProjectClient.asked))
    state.projects = kept_projects
    state.client = kept_client

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
    check(os.path.basename(created) == "sh01_v001.nk",
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
    check(os.path.basename(bumped) == "sh01_v002.nk",
          "save_next_version increments (%s)" % os.path.basename(bumped))
    check(stamp.read().version == 2, "and the stamp follows the version")

    # A foreign script in the folder must not move the version on.
    (Path(created).parent / "old_comp_backup.nk").write_text("", encoding="utf-8")
    (Path(created).parent / "PizzaHunt_sc01_sh01_Lighting_v009.nk").write_text(
        "", encoding="utf-8")
    check(len(fetch.list_versions(context)) == 2,
          "scripts from another task are ignored")

    # A Kitsu Write left aimed at the previous version is worse than one
    # never set: the frames land somewhere plausible and wrong, and the first
    # anybody knows is a comp reading a render from an older script.
    from BB_core import frames as nuke_frames
    from BB_pipeline_nuke import review as nuke_review
    from BB_pipeline_nuke import writenode as _writenode

    aimed = _writenode.create()
    before = aimed.value_of("file")
    check("v002" in before, "a fresh Write aims at the open version (%s)"
          % os.path.basename(before or ""))

    bumped_again = scripts.save_next_version()
    after = aimed.value_of("file")
    check("v003" in after,
          "versioning up moves the Write with it (%s)" % os.path.basename(after or ""))
    check("v002" not in after, "and off the version before it")
    check(os.path.basename(bumped_again).endswith("v003.nk"),
          "the script itself is v003 too (%s)" % os.path.basename(bumped_again))

    # -- opening no longer asks about work nobody changed --------------------
    # The add-on stamps the root and repoints Writes, so nuke.modified() is
    # true after any pipeline action whether or not the artist touched
    # anything. open_version used to prompt on that, which meant a prompt on
    # scripts nobody had changed.
    nuke.answer = False          # "no" to anything still asking
    asked_before = len(nuke.messages)
    reopened = scripts.open_version(created, context)
    check(reopened == created,
          "opening does not ask about unsaved work (%s)" % reopened)
    check(len(nuke.messages) == asked_before,
          "and says nothing about it either")
    nuke.answer = True

    # Asked each time by default: the answer genuinely changes, between a
    # comp being finished with and one still wanted for copying nodes out of.
    check(settings.get("open_in", "ask") == "ask",
          "opening offers the choice by default (%s)"
          % settings.get("open_in", "ask"))

    # -- opening from the browser is enough to know the project --------------
    # A script written before it was stamped, or by hand, carries nothing to
    # identify it. The browser knew which project it opened from, and
    # throwing that away is how a file opened from the browser ends up
    # unable to say which project it is in.
    kept_name = nuke.root().name()
    kept_context = state.context
    kept_stamp = nuke._root.knobs.get("BB_pipeline")

    unstamped = Path(work_root) / "sc01" / "sh01" / "Compositing" / "sh01_v001.nk"
    unstamped.parent.mkdir(parents=True, exist_ok=True)
    unstamped.write_text("# no stamp here", encoding="utf-8")

    nuke.scriptClear()
    state.context = None
    scripts.open_version(str(unstamped), context)

    recovered, source = stamp.read_current()
    check(recovered is not None and source == "session",
          "the browser's context survives opening an unstamped script (%s)"
          % source)
    check(recovered is not None and recovered.project_id == context.project_id,
          "and it still knows the project")

    # A context left over from another script must not be borrowed.
    nuke.scriptClear()
    nuke._root._name = str(Path(work_root) / "elsewhere" / "other_v003.nk")
    borrowed, source = stamp.read_current()
    check(source != "session",
          "but another script does not borrow it (%s)" % source)

    # Put the session back where the rest of the checks expect it - the
    # stub records every open, and a later check counts them.
    del nuke.opened[:]
    nuke._root._name = kept_name
    state.context = kept_context
    if kept_stamp is not None:
        nuke._root.knobs["BB_pipeline"] = kept_stamp

    # -- a shot with no frame range still lists its tasks ---------------------
    # frames.frame_range returns None rather than a pair when a shot carries
    # no frame data, and plenty do. Unpacking it raised in the caption above
    # the task rows, so the shot looked like it simply had no tasks - and
    # then nothing could be created, because no task could be picked.
    bare = {"id": "sh9", "name": "Stills", "parent_id": "s1"}
    state.shots = state.shots + [bare]
    state.tasks = [{"id": "t9", "task_type_id": "tt2"}]

    check(nuke_frames.frame_range(bare) is None,
          "a shot with no frame data has no range (%r)"
          % (nuke_frames.frame_range(bare),))
    check([state.task_type_name(t["task_type_id"])
           for t in state.browsable_tasks()] == ["Compositing"],
          "and its tasks are still there to list")

    # The caption is what used to raise; it has to cope on its own.
    facts = []
    span = nuke_frames.frame_range(bare)
    if span:
        facts.append("%d-%d" % span)
    rate = nuke_frames.fps(state.projects[0], bare)
    if rate:
        facts.append(nuke_frames.describe(rate))
    check(facts == ["24 fps"],
          "the caption drops the range it does not have (%s)" % facts)

    # -- a Write has to render into this script's own version folder ---------
    # A Write copied in from another shot keeps that shot's path, and
    # publishing it would post one shot's frames against another - the same
    # mistake Blender made by holding a render across a file load.
    mine = nuke_review._belongs_elsewhere(
        context, str(Path(work_root) / "sc01" / "sh01" / "Compositing"
                     / "Render" / "v004" / "sh01_v004.%04d.exr"))
    check(not mine, "a Write in this version's folder is fine (%s)" % mine)

    theirs = nuke_review._belongs_elsewhere(
        context, str(Path(work_root) / "sc01" / "sh02" / "Compositing"
                     / "Render" / "v001" / "sh02_v001.%04d.exr"))
    check("not the render folder" in theirs,
          "another shot's Write is refused (%s)" % theirs)
    check("sh02" in theirs, "and the message names where it points")

    # -- one frame is an image, a run of them is a movie ---------------------
    # The Blender side learned this the hard way: an MP4 of one frame is a
    # flicker at video quality where Kitsu would have shown a picture.
    one_frame_dir = Path(work_root) / "onef"
    one_frame_dir.mkdir(parents=True, exist_ok=True)
    single = str(one_frame_dir / "sh01_v009.%04d.exr")
    (one_frame_dir / "sh01_v009.1001.exr").write_bytes(b"exr")

    made, sequence, problem = nuke_review.prepare(single)
    check(not problem, "one frame prepares without complaint (%s)" % problem)
    check(made is not None and str(made).lower().endswith(".png"),
          "and goes as an image, not a movie (%s)" % made)
    check(sequence is False, "and is not counted as a sequence")

    (one_frame_dir / "sh01_v009.1002.exr").write_bytes(b"exr")
    made, sequence, problem = nuke_review.prepare(single)
    check(made is not None and str(made).lower().endswith(".mp4"),
          "a second frame makes it a movie (%s)" % made)
    check(sequence is True, "and that is a sequence")

    # -- the setting means the same thing in both applications ----------------
    settings.save({"version_up_on_publish": "NEVER"})
    held = stamp.read().version
    nuke_review._version_up_after_publish(True)
    check(stamp.read().version == held, "NEVER leaves the version alone")

    settings.save({"version_up_on_publish": "SEQUENCE"})
    nuke_review._version_up_after_publish(False)
    check(stamp.read().version == held,
          "SEQUENCE leaves it alone for a single image")
    nuke_review._version_up_after_publish(True)
    check(stamp.read().version == held + 1,
          "and cuts it for a sequence (%s)" % stamp.read().version)

    # A true/false written by an older build still has to be understood.
    settings.save({"version_up_on_publish": False})
    held = stamp.read().version
    nuke_review._version_up_after_publish(True)
    check(stamp.read().version == held, "an old false still means never")
    settings.save({"version_up_on_publish": "SEQUENCE"})

    # Publishing closes the version it came from, so three renders published
    # from one saved script cannot all land against the same version with
    # nothing on disk telling them apart.
    before_publish = stamp.read().version
    moved = nuke_review._version_up_after_publish()
    check(stamp.read().version == before_publish + 1,
          "publishing cuts the next version (%s -> %s)"
          % (before_publish, stamp.read().version))
    check("v%03d" % (before_publish + 1) in moved,
          "and says which one it is now (%s)" % moved)

    settings.save({"version_up_on_publish": False})
    try:
        held = stamp.read().version
        nuke_review._version_up_after_publish()
        check(stamp.read().version == held,
              "and leaves it alone when the setting says so")
    finally:
        settings.save({"version_up_on_publish": True})


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
    # Most shots on a young show have no preview yet, so "no thumbnail" is
    # the common case and has to be distinguishable from a broken feature.
    check(thumbnails.pixmap(client, without) is None,
          "a shot with no preview yields no pixmap")
    source = pathlib.Path(REPO / "nuke" / "BB_pipeline_nuke" / "browser.py").read_text(
        encoding="utf-8")
    check("no thumbnail in Kitsu" in source,
          "and the browser says so rather than going blank")

    thumbnails.clear()
    check(not thumbnails._cache, "the cache empties on clear")

    # -- snapshots, which is how a comp gets a thumbnail at all ---------------
    from BB_pipeline_nuke import capture

    check(capture.source_node() is None,
          "nothing selected and no viewer means nothing to snapshot")
    picture, problem = capture.snapshot()
    check(picture is None and "nothing to snapshot" in problem,
          "and it says why rather than failing silently (%r)" % problem)
    check(capture.describe() == "", "and the dialog is told there is nothing")

    node = nuke.StubNode("Merge1")
    nuke.selected = [node]
    check(capture.source_node() is node, "one selected node is the source")
    check(capture.describe() == "Merge1", "and the dialog names it")

    picture, problem = capture.snapshot(frame=1005)
    check(picture is not None and Path(picture).is_file(),
          "a snapshot renders a file (%s)" % picture)
    check(problem == "", "with no problem to report")
    span = str(nuke.executed[-1][1:]) if nuke.executed else "none"
    check(nuke.executed and nuke.executed[-1][1:] == (1005, 1005),
          "one frame only (%s)" % span)
    written = nuke.created[-1].knobs
    check(written.get("file_type") == "png", "as a PNG")
    check(written.get("colorspace") == capture.REVIEW_COLORSPACE,
          "written through sRGB, or a linear comp arrives flat (%s)"
          % written.get("colorspace"))
    check(nuke.deleted and nuke.deleted[-1] is nuke.created[-1],
          "and the Write node is removed again")
    check(nuke.created[-1].knobs.get("inputs") == [node],
          "wired to the source rather than to the selection")

    # Two selected nodes is ambiguous, so it falls back to the Viewer.
    nuke.selected = [node, nuke.StubNode("Merge2")]
    nuke.viewer_input = nuke.StubNode("FromViewer")
    check(capture.describe() == "FromViewer",
          "an ambiguous selection defers to the Viewer (%s)" % capture.describe())
    nuke.selected = [node]

    # A Write that will not build has to come back with the reason.
    def broken(**knobs):
        raise RuntimeError("unknown knob create_directories")

    real_write = nuke.nodes.Write
    nuke.nodes.Write = broken
    failed, why = capture.snapshot()
    nuke.nodes.Write = real_write
    check(failed is None and "could not render" in why,
          "a Write that will not build reports why (%r)" % why)

    # -- a snapshot reaches Kitsu through the shared call ---------------------
    class PreviewClient(StubClient):
        published = []

        def publish_preview(self, task_id, file_path, comment="",
                            task_status_id=None, normalize=False, log=None):
            PreviewClient.published.append((task_id, file_path, comment))
            return {"comment": {"id": "c1"}, "preview": {"id": "p1"}}

    state.client = PreviewClient()
    note = publish.send(context, created, comment="with a picture",
                        task_status_id="st9", preview=picture)
    check(PreviewClient.published, "a preview goes through publish_preview")
    check(PreviewClient.published[-1][1] == picture, "with the rendered frame")
    check("snapshot" in state.message, "and the message says so (%r)" % state.message)

    capture.discard(picture)
    check(not Path(picture).exists(), "the temporary frame is cleaned up")
    state.client = StubClient()

    # -- the Kitsu Write node -------------------------------------------------
    from BB_pipeline_nuke import review as nuke_review
    from BB_pipeline_nuke import writenode

    stamp.write(context.at_version(4))
    nuke.selected = []

    node = writenode.create()
    check(writenode.is_ours(node), "the Write is marked as ours")
    check(writenode.stream_of(node) == "main", "and knows its stream")

    written = node.knob("file").value()
    check(written.endswith(".exr") and "%04d" in written,
          "its path is a frame pattern (%s)" % os.path.basename(written))
    check("/Render/" in written.replace(chr(92), "/") and "v004" in written,
          "under the version it belongs to (%s)" % written)
    check(written == written.replace(chr(92), "/"),
          "with forward slashes, which is what Nuke wants")

    for knob in ("bb_set_path", "bb_add_read", "bb_publish"):
        check(node.knob(knob) is not None, "%s button is on the node" % knob)

    # A script with no context must refuse before making anything.
    made_before = len(nuke.created)
    del nuke._root.knobs[stamp.KNOB]
    nuke._root._name = "Root"
    try:
        writenode.create()
        refused = False
    except writenode.WriteError:
        refused = True
    check(refused, "a script with no Kitsu context refuses to make a Write")
    check(len(nuke.created) == made_before, "and makes nothing on the way out")
    stamp.write(context.at_version(4))

    # -- finding what was rendered --------------------------------------------
    pattern = written
    check(nuke_review.rendered_frames(pattern) == [],
          "nothing rendered yet means no frames")

    folder = Path(pattern).parent
    folder.mkdir(parents=True, exist_ok=True)
    stem = Path(pattern).name.replace("%04d", "%d")
    for frame in (1001, 1002, 1003):
        (folder / (stem % frame)).write_bytes(b"exr")

    found = nuke_review.rendered_frames(pattern)
    check(len(found) == 3, "the rendered frames are found (%d)" % len(found))
    check(nuke_review.frame_span(pattern, found) == (1001, 1003),
          "and their range is read off the names (%s)"
          % str(nuke_review.frame_span(pattern, found)))

    read = writenode.add_read(node)
    check(read is not None and read.knobs.get("file") == pattern,
          "Add Read Node reads exactly what was written")

    # Importing a render from a task Nuke does not author goes through the
    # same shape: a placeholder path and an explicit range, never one frame.
    brought = scripts.read_sequence(pattern, 1001, 1003)
    check(brought is not None and brought.knobs.get("file") == pattern,
          "a render imports as a Read on the sequence pattern")
    check(brought.knobs.get("first") == 1001 and brought.knobs.get("last") == 1003,
          "over the range that was rendered (%s-%s)"
          % (brought.knobs.get("first"), brought.knobs.get("last")))
    try:
        scripts.read_sequence("", 1, 2)
        refused = False
    except scripts.ScriptError:
        refused = True
    check(refused, "and an empty path is refused rather than read")
    check(read.knobs.get("first") == 1001 and read.knobs.get("last") == 1003,
          "over the rendered range")

    # -- the review movie ------------------------------------------------------
    movie, problem = nuke_review.build_movie(pattern)
    check(problem == "" and movie and movie.endswith(".mp4"),
          "a review movie is built as mp4 (%s / %r)"
          % (os.path.basename(movie or ""), problem))
    built = nuke.created[-1]
    check(built.knobs.get("file_type") == "mov",
          "through the mov writer (%s)" % built.knobs.get("file_type"))
    check(built.knobs.get("colorspace") == nuke_review.REVIEW_COLORSPACE,
          "written through sRGB")
    check(all(n in nuke.deleted for n in nuke.created[-2:]),
          "and the temporary Read and Write are removed")
    nuke_review.discard(movie)

    check(nuke_review.as_glob("a/b_v001.%04d.exr") == "a/b_v001.*.exr",
          "printf patterns glob")
    check(nuke_review.as_glob("a/b_v001.####.exr") == "a/b_v001.*.exr",
          "and so do hashes")

    # -- the three things reported from the first real use ---------------------
    stamp.write(context.at_version(4))
    nuke.selected = []
    fresh = writenode.create()

    check(fresh.knobs.get("create_directories") is True,
          "create_directories is on, so a first render does not fail on a "
          "missing folder")

    package.install_menu()
    nodes_menu = nuke.menu("Nodes").menus.get(package.MENU)
    check(nodes_menu is not None,
          "the node is registered in the Nodes menu, which is what Tab searches")
    check(any(name == "Write Kitsu" for name, _c, _s in nodes_menu.commands),
          "under a findable name (%s)"
          % [n for n, _c, _s in (nodes_menu.commands if nodes_menu else [])])

    # evaluate() on a File_Knob resolves to the *current* frame, so using it
    # for a glob finds nothing unless the playhead happens to sit on a
    # rendered frame. That is what made a finished render look unrendered.
    written = fresh.knob("file").value()
    fresh.knob("file")._value = written
    concrete = written.replace("%04d", "9999")
    fresh._objects["file"]._value = written

    check(nuke_review.has_frame_pattern(written),
          "the raw knob value keeps its frame pattern")
    check(not nuke_review.has_frame_pattern(concrete),
          "an evaluated one does not")
    check(writenode.pattern_of(fresh) == written,
          "so pattern_of returns the pattern, not one frame")

    folder2 = Path(written).parent
    folder2.mkdir(parents=True, exist_ok=True)
    stem2 = Path(written).name.replace("%04d", "%d")
    for frame in (1001, 1002):
        (folder2 / (stem2 % frame)).write_bytes(b"exr")
    through_pattern = nuke_review.rendered_frames(writenode.pattern_of(fresh))
    through_evaluated = nuke_review.rendered_frames(concrete)
    check(len(through_pattern) >= 2,
          "and the rendered frames are found through it (%d)" % len(through_pattern))
    check(through_evaluated == [],
          "where the evaluated path finds nothing, which was the bug")

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
