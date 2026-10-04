"""JOS-side integration test, against the real ``jos`` module.

Unlike nuke_check.py this needs no stub: JOS ships ``jos-python``, its
headless interpreter (Nuke's ``nuke -t``, with no licence), so the projects,
Writes, renders and review movies here are real. Only Kitsu is stubbed.

    jos-python --no-plugins tests/jos_check.py

What this does not cover is the Qt browser and dialogs, which need the app.
"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "jos"))

failures = []


def check(condition, description):
    print(("  ok   " if condition else "  FAIL ") + description)
    if not condition:
        failures.append(description)


def main():
    import jos

    root = Path(tempfile.mkdtemp(prefix="kitsu_jos_"))
    os.environ["BB_PIPELINE_SETTINGS"] = str(root / "settings.json")

    from BB_core import settings, workfiles
    settings.save({"work_root": str(root / "work"),
                   "render_root": str(root / "renders"),
                   "publish_on_save": True,
                   "version_up_on_publish": "NEVER"})

    import BB_pipeline_jos as package
    from BB_pipeline_jos import (capture, fetch, publish, review, scripts,
                                 session, stamp, writenode)

    check(package.core.available,
          "shared core located (%s)" % (package.core.error or "ok"))
    check(not jos.has_ui(), "running headless (jos-python)")
    check(package.install_menu() is None, "and the menu stays out of it")

    state = session.state

    # -- shots only, compositing only ----------------------------------------
    check(session.DCC == "jos", "the DCC is jos")
    check(settings.config().dcc("jos")["ext"] == "joscomp",
          "and writes .joscomp files")
    LIGHTING = {"id": "d1", "name": "Lighting"}
    COMP = {"id": "d2", "name": "Compositing"}
    state.departments = {d["id"]: d for d in (LIGHTING, COMP)}
    state.task_types = {
        "tt1": {"id": "tt1", "name": "Lighting", "department_id": "d1"},
        "tt2": {"id": "tt2", "name": "Compositing", "department_id": "d2"},
    }
    state.tasks = [{"id": "t1", "task_type_id": "tt1"},
                   {"id": "t2", "task_type_id": "tt2"}]
    state.task_departments = session.departments_for_dcc()
    offered = [state.task_type_name(t["task_type_id"]) for t in state.comp_tasks()]
    check(offered == ["Compositing"],
          "only compositing tasks are JOS's to author (%s)" % offered)

    state.projects = [{"id": "p1", "name": "PizzaHunt", "code": None, "fps": "24"}]
    state.sequences = [{"id": "s1", "name": "sc01", "parent_id": "p1"}]
    state.shots = [{"id": "sh1", "name": "sh01", "parent_id": "s1",
                    "nb_frames": 252,
                    "data": {"frame_in": 1001, "frame_out": 1252}}]

    context = fetch.current_context("p1", "s1", "sh1", "t2")
    check(context is not None and context.task == "Compositing",
          "context builds from a shot selection")

    # -- a new version: a real project on disk -------------------------------
    created = scripts.create_version(context)
    check(created.endswith("_v001.joscomp") and os.path.isfile(created),
          "Create v001 saves a project (%s)" % os.path.basename(created))
    check(Path(created).parent.name == "2DScene",
          "in the task's 2DScene folder (%s)" % Path(created).parent)
    check(jos.project_path() == created.replace(os.sep, "/"),
          "and it is the open project (%s)" % jos.project_path())
    check(not jos.modified(), "with nothing unsaved")
    stamped = stamp.read()
    check(stamped is not None and stamped.version == 1
          and stamped.task_id == "t2", "stamped with its context")
    check((jos.first_frame(), jos.last_frame(), jos.fps()) == (1001, 1252, 24.0),
          "frame range and fps from Kitsu (%s-%s @ %s)"
          % (jos.first_frame(), jos.last_frame(), jos.fps()))

    # -- a Kitsu Write, rendered for real ------------------------------------
    plate = jos.create_node("Constant", name="Plate", color=(0.5, 0.25, 0.1, 1))
    write = writenode.create()
    write.set_input(0, plate)
    check(write.name == "KitsuWrite1", "a Kitsu Write (%s)" % write.name)
    check(writenode.is_ours(write) and writenode.stream_of(write) == "main",
          "carrying its stream")
    check("bb_stream" in write and jos.node(write.name)["bb_stream"] == "main",
          "in a hidden parameter")
    pattern = write["file"]
    check(pattern.endswith(".%04d.exr") and "2DRenders" in pattern,
          "pointed at this version's render folder (%s)" % pattern)
    check("write_set_path" in write["bb_set_path"]
          and "jos.this_node()" in write["bb_publish"],
          "with buttons that call back into the plugin")
    check(write["bb_info"] == "sh01_v001", "showing its version")

    jos.render(write.name, 1001, 1003)
    found = review.rendered_frames(pattern)
    check(len(found) == 3, "rendering writes the frames (%d)" % len(found))
    check(review.frame_span(pattern) == (1001, 1003), "read back as 1001-1003")

    read = writenode.add_read(write)
    check(read is not None and read["file"] == pattern
          and (read["first"], read["last"]) == (1001, 1003),
          "Add Read Node reads what was rendered")
    jos.delete(read)

    # -- review media: built by JOS, temporary nodes gone again ---------------
    before = sorted(n.name for n in jos.all_nodes())
    was_modified = jos.modified()
    still, problem = review.build_still(pattern, 1002)
    check(still and os.path.getsize(still) > 0,
          "a still for Kitsu (%s)" % (problem or os.path.basename(still)))
    movie, problem = review.build_movie(pattern)
    check(movie and movie.endswith(".mp4") and os.path.getsize(movie) > 0,
          "an H.264 movie for Kitsu (%s)" % (problem or os.path.basename(movie or "")))
    check(sorted(n.name for n in jos.all_nodes()) == before,
          "leaving the comp as it was")
    check(jos.modified() == was_modified, "and its modified flag too")
    for made in (still, movie):
        review.discard(made)

    picture = str(root / "snap.png")
    capture.render_png(plate, picture, 1001)
    check(os.path.getsize(picture) > 0, "a snapshot renders the chosen node")
    check(capture.describe() == "", "headless, nothing is selected or viewed")

    # -- publishing, against a stub Kitsu ------------------------------------
    state.client = None
    check("not connected" in publish.why_not(stamp.read()),
          "no publish without a Kitsu session")

    class StubClient:
        logged_in = True
        host = "http://kitsu.test"
        previews = []

        def task(self, task_id):
            return {"task_status_id": "st1"}

        def publish_preview(self, task_id, preview, **kwargs):
            StubClient.previews.append((task_id, os.path.basename(preview),
                                        os.path.getsize(preview), kwargs))

    state.client = StubClient()
    jos.save()
    note = review.submit(write, comment="first look", task_status_id="st9")
    check("updated" in note.lower(), "publishing a render reports success (%r)" % note)
    sent = StubClient.previews[-1] if StubClient.previews else ("", "", 0, {})
    check(sent[0] == "t2" and sent[1].endswith(".mp4") and sent[2] > 0,
          "an mp4 went to the task (%s)" % (sent[:3],))
    check(sent[3].get("comment", "").startswith("first look")
          and sent[3].get("task_status_id") == "st9",
          "with the comment and status")

    # -- versioning up --------------------------------------------------------
    path = scripts.save_next_version()
    check(path.endswith("_v002.joscomp") and os.path.isfile(path),
          "Save Next Version writes v002 (%s)" % os.path.basename(path))
    check(stamp.read().version == 2, "stamped v002")
    check("_v002" in write["file"] and write["bb_info"] == "sh01_v002",
          "the Kitsu Write follows (%s)" % write["file"])
    check(not jos.modified(), "and nothing is left unsaved")

    # -- open and import ------------------------------------------------------
    opened = scripts.open_version(created)
    check(opened == created and stamp.read().version == 1,
          "opening v001 brings its stamp back")
    check(jos.node("KitsuWrite1")["file"].find("_v001") > 0,
          "and its Writes as they were saved")
    names = jos.import_project(path)
    check("KitsuWrite2" in names, "importing v002 pastes its nodes (%s)" % names)
    try:
        scripts.import_into_current(str(root / "gone.joscomp"))
        refused = False
    except scripts.ScriptError:
        refused = True
    check(refused, "a missing file is refused rather than imported")

    print()
    if failures:
        print("%d check(s) failed" % len(failures))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
