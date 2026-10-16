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


# A Nuke 16 script as the Nuke integration saves it: the Kitsu context in the
# root's hidden BB_pipeline knob, a Kitsu Write with its Kitsu tab. Written by
# a real Nuke 16.0v6 (BB_pipeline_nuke.stamp.write and writenode._add_tab),
# with the machine-specific root name left out.
NUKE_SCRIPT = """#! C:/Program Files/Nuke16.0v6/nuke-16.0.6.dll -nx
version 16.0 v6
Root {
 inputs 0
 first_frame 1001
 last_frame 1252
 format "2048 1556 0 0 2048 1556 1 2K_Super_35(full-ap)"
 colorManagement Nuke
%(stamp)s}
Constant {
 inputs 0
 color {0.5 0.25 0.1 1}
 name Plate
}
Write {
 file X:/elsewhere/sh02_v001.%%04d.exr
 file_type exr
 name KitsuWrite1
 addUserKnob {20 bb_kitsu l Kitsu}
 addUserKnob {1 bb_stream l Stream +INVISIBLE}
 bb_stream main
 addUserKnob {26 bb_info l Version T sh02_v001}
 addUserKnob {22 bb_set_path l "Set Output Path" -STARTLINE T "import BB_pipeline_nuke as _bb; _bb.write_set_path(nuke.thisNode())"}
 addUserKnob {22 bb_add_read l "Add Read Node" -STARTLINE T "import BB_pipeline_nuke as _bb; _bb.write_add_read(nuke.thisNode())"}
 addUserKnob {22 bb_publish l "Publish to Kitsu" -STARTLINE T "import BB_pipeline_nuke as _bb; _bb.write_publish(nuke.thisNode())"}
}
"""


def nuke_script(path, entity_context=None):
    """Write a .nk version, stamped as Nuke stamps it when given a context."""
    import json

    stamp = ""
    if entity_context is not None:
        raw = json.dumps(entity_context.to_dict())
        for char in ("\\", "{", "}", '"'):
            raw = raw.replace(char, "\\" + char)
        stamp = (' addUserKnob {20 User}\n'
                 ' addUserKnob {1 BB_pipeline l "Kitsu context" +INVISIBLE}\n'
                 ' BB_pipeline "%s"\n' % raw)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(NUKE_SCRIPT % {"stamp": stamp}, encoding="utf-8")
    return str(path)


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

    # -- one compositing history with Nuke ------------------------------------
    # A task comped in Nuke first: its .nk versions are listed beside the
    # .joscomp ones, open by importing, and JOS carries on from their number.
    state.shots.append({"id": "sh2", "name": "sh02", "parent_id": "s1",
                        "data": {"frame_in": 1001, "frame_out": 1252}})
    comp = fetch.current_context("p1", "s1", "sh2", "t2")
    config = session.config_for(comp)
    check(workfiles.sharing_dccs(config, "jos") == ["jos", "nuke"],
          "the preset shares JOS's versions with Nuke's")
    nk = nuke_script(workfiles.work_file(comp, "nuke", 1, config),
                     comp.at_version(1))
    nk_bytes = Path(nk).read_bytes()
    check(Path(nk).parent == workfiles.scene_dir(comp, "jos", config)
          and Path(nk).parent.name == "2DScene",
          "Nuke's scripts sit in JOS's 2DScene folder (%s)" % Path(nk).parent)

    listed = fetch.list_versions(comp)
    check([(v, Path(p).name) for v, p in listed] == [(1, "sh02_v001.nk")],
          "the browser lists the Nuke version (%s)" % listed)
    check(scripts.is_nuke(listed[0][1]) and not scripts.is_nuke(created),
          "marked as a Nuke file")
    path, version = scripts.next_version(comp)
    check(version == 2 and path.name == "sh02_v002.joscomp",
          "Create would make v002 (%s)" % path.name)

    imported = scripts.open_nuke(nk, comp)
    check(imported is not None and "Plate" in imported[0]
          and "KitsuWrite1" in imported[0],
          "opening the .nk imports it (%s)" % (imported,))
    check(jos.project_path() == "", "into a new, unsaved project")
    got = stamp.read()
    check(got is not None and got.version == 1 and got.task_id == "t2"
          and got.entity_id == "sh2" and got.server == "http://kitsu.test",
          "carrying the Kitsu stamp the Nuke script had (%s)"
          % (got.to_dict() if got else None))
    kitsu_write = jos.node("KitsuWrite1")
    check(kitsu_write is not None and writenode.is_ours(kitsu_write)
          and "bb_set_path" in kitsu_write and "bb_publish" in kitsu_write,
          "Nuke's Kitsu Write gets JOS's Kitsu buttons")
    check((jos.first_frame(), jos.last_frame()) == (1001, 1252),
          "and the script's frame range")

    upcoming, version = scripts.upcoming_version()
    check(version == 2 and upcoming.name == "sh02_v002.joscomp",
          "Save Next Version offers v002.joscomp (%s)" % upcoming.name)
    saved = scripts.save_next_version()
    check(saved.endswith("sh02_v002.joscomp") and os.path.isfile(saved)
          and jos.project_path() == saved.replace(os.sep, "/"),
          "and saves it (%s)" % os.path.basename(saved))
    check(stamp.read().version == 2, "stamped v002")
    check("_v002" in kitsu_write["file"] and "2DRenders" in kitsu_write["file"]
          and kitsu_write["bb_info"] == "sh02_v002",
          "the imported Kitsu Write renders to v002 (%s)" % kitsu_write["file"])
    check(Path(nk).read_bytes() == nk_bytes, "the .nk is never written")
    listed = fetch.list_versions(comp)
    check([(v, Path(p).suffix) for v, p in listed]
          == [(1, ".nk"), (2, ".joscomp")],
          "one history: v001.nk, v002.joscomp (%s)" % listed)

    before = len(jos.all_nodes())
    scripts.import_into_current(nk)
    check(len(jos.all_nodes()) == before + 2,
          "importing a .nk version pastes its nodes (%d -> %d)"
          % (before, len(jos.all_nodes())))
    check(all("bb_set_path" in n for n in jos.all_nodes("Write")
              if writenode.is_ours(n)),
          "with JOS's Kitsu buttons on its Write")
    check(Path(nk).read_bytes() == nk_bytes, "still without touching the .nk")

    # A script saved in Nuke without the pipeline's stamp: the browser's
    # context, at the version in its name.
    bare = nuke_script(workfiles.work_file(comp, "nuke", 3, config))
    check(scripts.open_version(bare, comp) == bare,
          "an unstamped .nk opens through open_version too")
    got = stamp.read()
    check(got is not None and got.version == 3 and got.task_id == "t2",
          "stamped from the browser's context at v003 (%s)"
          % (got.version if got else None))
    saved = scripts.save_next_version()
    check(saved.endswith("sh02_v004.joscomp"),
          "and Save Next Version makes v004 (%s)" % os.path.basename(saved))
    check(workfiles.next_workfile(comp, "nuke", config)[0].name == "sh02_v005.nk",
          "after which Nuke's next is v005.nk")

    print()
    if failures:
        print("%d check(s) failed" % len(failures))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
