"""Blender-side integration test - no Kitsu server required.

Not named test_*.py on purpose: it can only run inside Blender, and
unittest's discovery would import it in a plain Python and fail on bpy.

Runs the real add-on against a stubbed session cache, so the create / open /
increment loop can be tested offline and in CI:

    blender --background --factory-startup --python tests/blender_check.py

Exits non-zero on the first failure, so it is usable as a build gate.
"""

import base64
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "blender"))
# The add-on no longer puts BB_core on sys.path - Blender's extension
# policy forbids it, and the core is bound as a submodule instead. The
# test reaches for it directly, which is a harness's privilege.
sys.path.insert(0, str(REPO))

PACKAGE = "BB_pipeline"

failures = []


def check(condition, description):
    print(("  ok   " if condition else "  FAIL ") + description)
    if not condition:
        failures.append(description)


# -- stub Kitsu data -----------------------------------------------------------

PROJECT = {"id": "p1", "name": "Test Project", "code": "VIL", "fps": "24"}
SEQUENCE = {"id": "s1", "name": "FF9", "parent_id": "p1"}
SHOT = {"id": "sh1", "name": "0070", "parent_id": "s1",
        "nb_frames": 252, "data": {"frame_in": 1001, "frame_out": 1252}}
ASSET_TYPE = {"id": "at1", "name": "Prop"}
ASSET = {"id": "a1", "name": "knife", "entity_type_id": "at1"}

LIGHTING = {"id": "d1", "name": "Lighting"}
COMPOSITING = {"id": "d2", "name": "Compositing"}

TASK_TYPE = {"id": "tt1", "name": "precomp3d", "department_id": "d1"}
COMP_TYPE = {"id": "tt2", "name": "Compositing", "department_id": "d2"}
TASK = {"id": "t1", "task_type_id": "tt1", "entity_id": "sh1"}
COMP_TASK = {"id": "t2", "task_type_id": "tt2", "entity_id": "sh1"}


def install_bb_session(session):
    """Populate the session as though a login and two fetches had happened."""
    state = session.state
    state.client = type("OfflineClient", (), {
        "logged_in": True,
        "host": "https://kitsu.test",
        # Enough surface that a stray fetch answers instead of raising.
        "task": lambda self, task_id: {"task_status_id": "st1"},
        "tasks_for_shot": lambda self, shot_id: [TASK, COMP_TASK],
        "tasks_for_asset": lambda self, asset_id: [TASK],
    })()
    state.user = {"full_name": "Offline Test"}
    state.projects = [PROJECT]
    state.sequences = [SEQUENCE]
    state.shots = [SHOT]
    state.asset_types = [ASSET_TYPE]
    state.assets = [ASSET]
    state.tasks = [TASK, COMP_TASK]
    state.task_types = {TASK_TYPE["id"]: TASK_TYPE, COMP_TYPE["id"]: COMP_TYPE}
    state.departments = {LIGHTING["id"]: LIGHTING, COMPOSITING["id"]: COMPOSITING}
    # Blender's configured departments, as fetch._task_departments would set.
    state.task_departments = {"lighting", "modeling", "animation", "fx", "layout"}


def _prefs_config():
    from BB_pipeline import prefs
    return prefs.config()


def main():
    _bpy = bpy
    work_root = Path(tempfile.mkdtemp(prefix="bb_work_"))
    print("work root: %s" % work_root)

    import BB_pipeline
    from BB_pipeline import fetch, properties, session, stamp

    check(BB_pipeline.core.available,
          "shared core located (%s)" % (BB_pipeline.core.error or "ok"))

    # An add-on entry has to exist before AddonPreferences can be read; a
    # normal install creates it, and registering by hand here does not.
    bpy.context.preferences.addons.new().module = PACKAGE
    BB_pipeline.register()

    try:
        preferences = bpy.context.preferences.addons[PACKAGE].preferences
        preferences.server = "https://kitsu.test"
        preferences.email = "test@example.com"
        preferences.work_root = str(work_root)
        # Off for the file-loop checks; exercised on its own below.
        preferences.publish_on_save = False
        preferences.render_root = str(work_root / "renders")

        install_bb_session(session)

        # Re-read through properties.get() after anything that reloads a
        # file: the selectors live on the WindowManager, which gets
        # replaced, so a held reference goes stale.
        props = properties.get()
        with properties.suspend_updates():
            props.project = "p1"
            props.entity_type = "SHOT"
            props.sequence = "s1"
            props.shot = "sh1"
            props.task = "t1"

        # -- the per-DCC task filter ---------------------------------------------
        offered = [item[1] for item in properties.task_items(props, bpy.context)]
        check(offered == ["precomp3d"],
              "Blender offers only its own departments (got %s)" % offered)
        check("Compositing" not in offered,
              "a compositing task is not offered in Blender")

        shot_context = fetch.current_context()
        check(shot_context is not None, "context built from the selectors")
        check(shot_context.versioned(1) == "0070_v001",
              "context names to the studio scheme (got %s)" % shot_context.versioned(1))
        check(shot_context.project == "VIL", "project code preferred over its name")

        # -- empty shot ---------------------------------------------------------
        fetch.refresh_workfiles()
        check(len(session.state.workfiles) == 0, "a fresh shot lists no scene files")

        # -- create v001 --------------------------------------------------------
        result = bpy.ops.bb.new_workfile()
        check(result == {"FINISHED"}, "new_workfile succeeded (%s)" % result)

        expected = (work_root / "FF9" / "0070" / "precomp3d"
                    / "0070_v001.blend")
        check(expected.is_file(), "v001 written to the templated path")
        check(Path(bpy.data.filepath) == expected, "the new file is the one now open")

        stamped, source = stamp.read_current()
        check(source == "stamp", "the saved scene carries a stamped context")
        check(stamped.version == 1, "the stamp records v001")
        check(stamped.task_id == "t1", "the stamp keeps the Kitsu task id")
        check(stamped.entity_id == "sh1", "the stamp keeps the Kitsu shot id")

        fetch.refresh_workfiles()
        props = properties.get()
        check(len(session.state.workfiles) == 1, "the browser now lists one version")
        check(props.version == "1", "the version selector lands on the open file")

        # -- increment ----------------------------------------------------------
        result = bpy.ops.bb.save_next_version()
        check(result == {"FINISHED"}, "save_next_version succeeded (%s)" % result)

        v002 = expected.parent / "0070_v002.blend"
        check(v002.is_file(), "v002 written next to v001")
        check(Path(bpy.data.filepath) == v002, "v002 is now the open file")
        check(stamp.read_current()[0].version == 2, "the stamp followed the version")
        check(stamp.read_current()[0].task_id == "t1", "the ids survived the increment")

        # Versioning up has to move the render output with it. Left behind,
        # the frames land in the previous version's folder - plausible, and
        # wrong, and nobody notices until a comp reads the older render.
        output_now = bpy.context.scene.render.filepath
        check("v002" in output_now,
              "the output path followed the version (%s)" % output_now)
        check("v001" not in output_now,
              "and no longer points at the version before it")

        fetch.refresh_workfiles()
        props = properties.get()
        check([v for v, _ in reversed(session.state.workfiles)] == [2, 1],
              "versions list newest first")
        check(props.version == "2", "the selector follows the file just saved")

        # -- a foreign file must not move the version on ------------------------
        (expected.parent / "old_backup.blend").write_bytes(b"")
        (expected.parent / "VIL_FF9_0070_lighting_v009.blend").write_bytes(b"")
        fetch.refresh_workfiles()
        check(len(session.state.workfiles) == 2, "files from other tasks are ignored")

        result = bpy.ops.bb.new_workfile()
        check(result == {"FINISHED"}, "third new_workfile succeeded")
        check(properties.get().project == "p1",
              "the selection survives creating a version from the startup file")
        v003 = expected.parent / "0070_v003.blend"
        check(v003.is_file(), "next version is v003, not v010")

        # -- reopen -------------------------------------------------------------
        bpy.ops.wm.open_mainfile(filepath=str(expected))
        check(Path(bpy.data.filepath) == expected, "v001 reopened")

        reopened, source = stamp.read_current()
        check(source == "stamp", "the reopened file still carries its stamp")
        check(reopened.version == 1 and reopened.task_id == "t1",
              "context restored from the file with its ids intact")

        props = properties.get()
        check(props.project == "p1" and props.shot == "sh1" and props.task == "t1"
              and props.entity_type == "SHOT",
              "the load handler put the selectors back")

        # -- a missing root reports, it does not traceback ------------------------
        # Nested bpy.ops raise RuntimeError back into Python when the inner
        # operator reports an error; the browser must turn that into a message.
        from BB_pipeline import prefs as _prefs
        saved_root = preferences.work_root
        preferences.work_root = ""
        ready, why = _prefs.roots_ready()
        check(not ready and "Work Root" in why, "missing root is detected up front")
        # bpy.ops always re-raises a reported error to a Python caller, so what
        # is checked here is *which* message comes out: the guard's actionable
        # one, not the core exception leaking through a nested operator.
        try:
            bpy.ops.bb.new_workfile("EXEC_DEFAULT")
            message = ""
        except RuntimeError as error:
            message = str(error)
        check("work_root" in message or "Work Root" in message,
              "the acting button surfaces an actionable message (%r)" % message)
        check("paths.work_root" not in message,
              "the raw core exception does not leak through")
        preferences.work_root = saved_root

        # -- publish on save -----------------------------------------------------
        from BB_pipeline import publish
        sent = {}

        class StubClient:
            logged_in = True
            host = "https://kitsu.test"

            def tasks_for_shot(self, shot_id):
                return [TASK, COMP_TASK]

            def tasks_for_asset(self, asset_id):
                return [TASK]

            def task(self, task_id):
                return {"task_status_id": "st1"}

            def _request(self, method, path, **kwargs):
                sent["comment_only"] = kwargs.get("json", {}).get("comment")
                return {"id": "c1"}

            def publish_preview(self, task_id, file_path, comment="",
                                task_status_id=None, log=None):
                sent["task_id"] = task_id
                sent["comment"] = comment
                sent["status"] = task_status_id
                sent["file"] = file_path
                return {"comment": {"id": "c1"}, "preview": {"id": "p1"}}

        session.state.client = StubClient()
        ctx = stamp.read_current()[0]

        preferences.publish_on_save = False
        check(publish.why_not(bpy.context, ctx) != "",
              "publish is skipped when switched off")

        preferences.publish_on_save = True
        # Already the default, and set anyway because this half is about
        # what a save posts, not about what the default happens to be.
        preferences.preview_on_save = False   # no GPU in background mode

        check(type(preferences).bl_rna.properties['preview_on_save'].default
              is False,
              "a save does not attach a viewport preview unless asked")
        check(publish.why_not(bpy.context, ctx) == "",
              "a stamped context connected to Kitsu can publish")

        idless = session.EntityContext(project="VIL", group="a", entity="b",
                                       task="c", version=1)
        check("no Kitsu task" in publish.why_not(bpy.context, idless),
              "a context with no ids has nowhere to publish")

        # -- the comment and status dialog ----------------------------------------
        props = properties.get()
        check(properties.selected_status_id() is None,
              "status defaults to leaving the task alone")

        state = session.state
        state.statuses = [{"id": "st9", "name": "Waiting For Approval",
                           "short_name": "wfa"}]
        items = properties.status_items(props, bpy.context)
        check(items[0][0] == properties.KEEP_STATUS,
              "leave-unchanged is the first status offered")
        check(any(i[0] == "st9" for i in items),
              "Kitsu statuses are offered (%s)" % [i[1] for i in items])

        props.task_status = "st9"
        check(properties.selected_status_id() == "st9",
              "a chosen status is passed through")
        props.comment = "looks good"

        note = publish.send(bpy.context, ctx, Path("VIL_x_y_z_v001.blend"),
                            comment=props.comment,
                            task_status_id=properties.selected_status_id())
        check("Kitsu" in note, "send reports that Kitsu is being updated (%r)" % note)

        check(hasattr(bpy.ops.bb, "publish_save"), "publish dialog is registered")
        properties.clear(props, "task_status")
        preferences.publish_on_save = False

        # -- the Assets tab ------------------------------------------------------
        props = properties.get()
        with properties.suspend_updates():
            props.entity_type = "ASSET"
            props.asset_type = "at1"
            props.asset = "a1"
            props.task = "t1"

        asset_context = fetch.current_context()
        check(asset_context is not None, "asset context builds from the Assets tab")
        check(asset_context.entity_type == "asset", "context knows it is an asset")
        check(asset_context.versioned(1) == "knife_v001",
              "asset names use the same scheme (%s)" % asset_context.versioned(1))

        asset_dir = session.workfiles_module.work_dir(
            asset_context, _prefs_config())
        check("assets" in Path(asset_dir).parts,
              "assets sit under their own prefix (%s)" % asset_dir)

        with properties.suspend_updates():
            props.entity_type = "SHOT"

        # -- refresh picks up what Kitsu gained, without losing the selection -----
        class GrowingClient(StubClient):
            """Kitsu with a sequence added since the browser last looked."""

            def sequences(self, project_id):
                return [SEQUENCE, {"id": "s2", "name": "NewSeq", "parent_id": "p1"}]

            def shots_for_project(self, project_id):
                return [SHOT, {"id": "sh2", "name": "0080", "parent_id": "s2"}]

            def asset_types(self, project_id):
                return [ASSET_TYPE]

            def assets_for_project(self, project_id):
                return [ASSET]

            def task_types(self):
                return [TASK_TYPE, COMP_TYPE]

            def departments(self):
                return [LIGHTING, COMPOSITING]

            def task_statuses(self):
                return []

        session.state.client = GrowingClient()
        props = properties.get()
        with properties.suspend_updates():
            props.entity_type = "SHOT"
            props.sequence = "s1"
            props.shot = "sh1"
            props.task = "t1"

        session.state.sequences = []      # the cache goes stale
        session.state.shots = []
        fetch.refresh_project(bpy.context)

        check([q["name"] for q in session.state.sequences] == ["FF9", "NewSeq"],
              "refresh picks up a sequence added in Kitsu")
        check(len(session.state.shots) == 2, "refresh picks up the new shot")
        props = properties.get()
        check(props.sequence == "s1" and props.shot == "sh1" and props.task == "t1",
              "refresh keeps the selection it started with")

        session.state.client = StubClient()

        # -- append / link and previews -------------------------------------------
        from BB_pipeline import autoconnect, capture
        check(hasattr(bpy.ops.bb, "append_workfile")
              and hasattr(bpy.ops.bb, "link_workfile"),
              "Append and Link operators are registered")

        paths = bpy.context.preferences.filepaths
        paths.file_preview_type = "NONE"
        with capture.embedded_preview():
            check(paths.file_preview_type == "AUTO",
                  "thumbnails are forced on while the pipeline saves")
        check(paths.file_preview_type == "NONE",
              "the preview preference is put back afterwards")
        paths.file_preview_type = "AUTO"

        # No GPU in background mode, so this must return 0 rather than raise.
        made = capture.generate_datablock_previews(bpy.context)
        check(isinstance(made, int),
              "preview generation degrades quietly without a GPU (%r)" % made)

        # -- frame range and frame rate -------------------------------------------
        from BB_pipeline import scenesync

        class FramesClient(StubClient):
            served = {"id": "sh1", "name": "0070", "parent_id": "s1",
                      "nb_frames": 252,
                      "data": {"frame_in": 1001, "frame_out": 1252}}

            def shot(self, shot_id):
                return FramesClient.served

        session.state.client = FramesClient()
        shot_ctx = session.EntityContext(
            entity_type="shot", project="VIL", group="FF9", entity="0070",
            task="precomp3d", project_id="p1", group_id="s1", entity_id="sh1",
            task_id="t1", version=1)

        settings = scenesync.kitsu_settings(shot_ctx)
        check(settings["frame_start"] == 1001 and settings["frame_end"] == 1252,
              "frame range comes off the shot (%s)" % settings)
        check(settings["fps"] == 24 and settings["fps_base"] == 1.0,
              "frame rate comes off the project")

        scene = bpy.context.scene
        scene.frame_start, scene.frame_end = 1, 250
        diffs = scenesync.differences(scene, settings)
        check(len(diffs) >= 2, "a default scene disagrees with Kitsu (%s)" % diffs)

        applied = scenesync.apply(scene, settings)
        check(scene.frame_start == 1001 and scene.frame_end == 1252,
              "applying sets the scene range")
        check(scenesync.differences(scene, settings) == [],
              "and then nothing disagrees")
        check(scenesync.apply(scene, settings) == [],
              "applying twice is a no-op")

        # A shot whose range moved in Kitsu since the file was saved.
        FramesClient.served = dict(FramesClient.served,
                                   data={"frame_in": 1001, "frame_out": 1300})
        moved = scenesync.kitsu_settings(shot_ctx)
        check("end 1252 -> 1300" in scenesync.differences(scene, moved),
              "a range that moved in Kitsu is spotted (%s)"
              % scenesync.differences(scene, moved))

        preferences.frame_range_on_open = "APPLY"
        note = scenesync.on_open(bpy.context, shot_ctx)
        check(scene.frame_end == 1300, "APPLY mode fixes the scene")
        check("applied" in note.lower(), "and says so (%r)" % note)

        preferences.frame_range_on_open = "IGNORE"
        check(scenesync.on_open(bpy.context, shot_ctx) == "",
              "IGNORE mode says nothing")
        preferences.frame_range_on_open = "WARN"

        asset_ctx = session.EntityContext(entity_type="asset", project="VIL",
                                          group="Prop", entity="knife",
                                          task="Modeling", entity_id="a1",
                                          version=1)
        check(scenesync.kitsu_settings(asset_ctx) is None,
              "assets have no frame range")

        check(hasattr(bpy.ops.bb, "apply_frame_range"),
              "the fix operator is registered")
        session.state.client = StubClient()

        # -- the browser remembers where it was left --------------------------------
        from BB_pipeline import prefs as _p
        session.state.client = GrowingClient()
        props = properties.get()

        with properties.suspend_updates():
            props.entity_type = "ASSET"
            props.asset_type = "at1"
            props.asset = "a1"
            props.task = "t1"
        _p.remember(bpy.context)

        marked = _p.recall(bpy.context)
        check(marked["entity_type"] == "ASSET" and marked["asset"] == "a1",
              "the selection is bookmarked (%s)" % marked["entity_type"])

        # Loading a project clears the selectors on the way through. That must
        # not overwrite the bookmark with the blank state it passes through.
        fetch.project_selected(bpy.context)
        after = _p.recall(bpy.context)
        check(after["entity_type"] == "ASSET" and after["asset"] == "a1",
              "clearing for a project load does not clobber it (%s)" % after["entity_type"])

        props = properties.get()
        check(props.entity_type == "ASSET" and props.asset == "a1",
              "and the selection is put back afterwards (%s/%s)"
              % (props.entity_type, props.asset))

        # A bookmark pointing at something Kitsu no longer has is dropped.
        preferences.last_asset = "gone"
        fetch.project_selected(bpy.context)
        check(properties.get().asset != "gone",
              "a deleted entity is not restored")
        preferences.last_asset = "a1"

        session.state.client = StubClient()

        # -- render targets and review ---------------------------------------------
        from BB_pipeline import render, review

        for name in ("render_image", "render_animation", "render_playblast",
                     "submit_render"):
            check(hasattr(bpy.ops.bb, name), "%s is registered" % name)
        check(hasattr(bpy.types, "BB_PT_review"),
              "the review panel is registered for the render window")

        session.state.client = FramesClient()
        stamp.write(bpy.context.scene, shot_ctx.at_version(3))

        entity, stream, directory, stem, settings = render.target(
            bpy.context, render.ANIMATION)
        check(stream == "main", "an animation goes to the main stream")
        # The folders above already say the project, entity and task, so the
        # file only names the thing and its version.
        check(stem == "0070_v003",
              "the render is named off the entity and version (%s)" % stem)
        check(Path(directory).name == "v003"
              and "Render" in Path(directory).parts,
              "renders land in a per-version folder (%s)" % directory)
        check("main" not in Path(directory).parts,
              "with no folder for the main stream (%s)" % directory)
        check(settings.get("ext") == "exr", "the main stream is EXR")

        _, blast_stream, blast_dir, _, blast_settings = render.target(
            bpy.context, render.PLAYBLAST)
        check(blast_stream == "playblast", "a playblast has its own stream")
        check(blast_settings.get("ext") == "mp4",
              "playblasts are H.264 from the start")
        check(Path(blast_dir) != Path(directory),
              "a playblast does not overwrite the render")

        # A stamp that has been lost is not fatal for rendering: the filename
        # still parses, and rendering needs a version, not Kitsu ids. It is
        # fatal for *submitting*, which does need the task.
        del bpy.context.scene[stamp.key()]
        recovered, source = stamp.read_current()
        open_version = int(Path(bpy.data.filepath).stem.rsplit("_v", 1)[1])
        check(source == "filename" and recovered.version == open_version,
              "an unstamped but well-named file still resolves (%s v%s)"
              % (source, recovered.version))
        _, _, _, recovered_stem, _ = render.target(bpy.context, render.ANIMATION)
        check(recovered_stem.endswith("_v%03d" % open_version),
              "and renders under the version in its name (%s)" % recovered_stem)
        check(not recovered.task_id, "but carries no Kitsu task")

        session.state.last_render = {"directory": str(Path(directory)),
                                     "stem": stem, "context": recovered}
        check("no Kitsu task" in review.submit(bpy.context),
              "so submitting it is refused")
        stamp.write(bpy.context.scene, shot_ctx.at_version(3))

        # -- what review finds on disk --------------------------------------------
        render_dir = Path(directory)
        render_dir.mkdir(parents=True, exist_ok=True)
        for frame in (1001, 1002, 1003):
            (render_dir / ("%s.%d.exr" % (stem, frame))).write_bytes(b"")
        (render_dir / "something_else.1001.exr").write_bytes(b"")

        found = review.frames_on_disk(
            {"directory": str(render_dir), "stem": stem})
        check(len(found) == 3, "the render's own frames are found (%d)" % len(found))
        check(all(stem in Path(f).name for f in found),
              "and nothing belonging to another render")
        check([Path(f).name for f in found] == sorted(Path(f).name for f in found),
              "frames come back in order")

        lines = review.summary()
        session.state.last_render = {"directory": str(render_dir), "stem": stem,
                                     "context": shot_ctx}
        lines = review.summary()
        check(any("H.264" in line for line in lines),
              "the panel says EXR frames become H.264 (%s)" % lines)

        check(review.frames_on_disk({"directory": str(render_dir),
                                     "stem": "nothing_here"}) == [],
              "a render with no frames yet reports none")

        # -- a single still ---------------------------------------------------------
        still_dir = render_dir.parent / "stills"
        still_dir.mkdir(parents=True, exist_ok=True)

        # What Render Image writes now: the frame number is in the name, the
        # same as every other frame.
        numbered = still_dir / ("%s.1001.exr" % stem)
        numbered.write_bytes(b"")
        found = review.frames_on_disk({"directory": str(still_dir), "stem": stem})
        check([Path(f).name for f in found] == [numbered.name],
              "a numbered still is found (%s)" % [Path(f).name for f in found])

        session.state.last_render = {"directory": str(still_dir), "stem": stem,
                                     "context": shot_ctx}
        check(any("PNG" in line for line in review.summary()),
              "and reports that it becomes a PNG (%s)" % review.summary())

        # A still written before that fix had no frame number at all, and
        # finding nothing is what produced "nothing rendered to submit".
        numbered.unlink()
        (still_dir / ("%s.exr" % stem)).write_bytes(b"")
        found = review.frames_on_disk({"directory": str(still_dir), "stem": stem})
        check(len(found) == 1,
              "an unnumbered still is found too (%d)" % len(found))

        # -- uploads are capped to a review size ------------------------------------
        preferences.review_max_width = 1920
        check(review.review_size(bpy.context, 3840, 2160) == (1920, 1080),
              "4K is halved for review")
        check(review.review_size(bpy.context, 1280, 720) == (1280, 720),
              "and anything already smaller is left alone")
        check(review.review_size(bpy.context, 1999, 1080)[1] % 2 == 0,
              "the scaled height stays even, or H.264 will not encode it")
        preferences.review_max_width = 0
        check(review.review_size(bpy.context, 3840, 2160) == (3840, 2160),
              "no cap - the default - uploads at full size")
        check(preferences.bl_rna.properties["review_max_width"].default == 0,
              "and full size is what a fresh install does")

        # -- a render made by hand is still publishable ----------------------
        # F12 renders into the Render Result buffer, writes nothing to disk,
        # and tells the add-on nothing - so a quick look could be rendered
        # and then not published, because as far as the pipeline was
        # concerned there was nothing to publish.
        session.state.last_render = None
        session.state.render_restore = None

        # The scene has to carry a context, the way one opened from the
        # browser does - that is what says where the frame belongs.
        with properties.suspend_updates():
            props_hand = properties.get()
            props_hand.entity_type = "SHOT"
            props_hand.sequence = SEQUENCE["id"]
            props_hand.shot = SHOT["id"]
            props_hand.task = TASK["id"]
        hand_context = fetch.current_context(bpy.context)
        stamp.write(bpy.context.scene, hand_context.at_version(2))

        # A stand-in with actual pixels. Blender never renders in background
        # mode, so the real Render Result datablock exists but carries no
        # image data and cannot be saved - which is a limit of the harness,
        # not of the add-on.
        made_up = bpy.data.images.new("pretend render", 32, 18)
        made_up.generated_color = (0.2, 0.4, 0.6, 1.0)
        try:
            bpy.context.scene.frame_current = 7
            filed = render._capture_manual_render(bpy.context.scene, made_up)
            check(bool(filed), "a hand-made render is filed (%s)" % filed)
            check(filed and Path(filed).is_file(),
                  "and actually written to disk")
            check(filed and ".0007." in Path(filed).name,
                  "under the frame it was rendered on (%s)"
                  % (Path(filed).name if filed else ""))
            check(session.state.last_render is not None,
                  "and recorded, so the review panel can send it")
            if session.state.last_render:
                check(session.state.last_render.get("kind") == render.IMAGE,
                      "as a still, not a sequence")
                found = review.frames_on_disk(session.state.last_render)
                check(len(found) == 1,
                      "and prepare finds exactly the one frame (%d)" % len(found))

            # Off by preference, nothing is filed.
            preferences.capture_manual_renders = False
            session.state.last_render = None
            check(not render._capture_manual_render(bpy.context.scene, made_up),
                  "and nothing is filed when the preference says not to")
            preferences.capture_manual_renders = True
        finally:
            bpy.data.images.remove(made_up)

        # -- an asset opened with no session still gets its render path -------
        # The roots live in the project's Kitsu brief, so finding them used
        # to need the browser to be pointed at that project. A .blend opened
        # from disk in a Blender that has never connected has no such
        # selection, and the output path was then silently not set at all.
        from BB_core import projects as core_projects

        kept_projects = session.state.projects
        kept_client = session.state.client
        core_projects.remember(PROJECT)

        asset_context = fetch.current_context(bpy.context)
        with properties.suspend_updates():
            props_asset = properties.get()
            props_asset.entity_type = "ASSET"
            props_asset.asset_type = ASSET_TYPE["id"]
            props_asset.asset = ASSET["id"]
            props_asset.task = TASK["id"]
        asset_context = fetch.current_context(bpy.context)
        check(asset_context is not None and asset_context.entity_type == "asset",
              "an asset context to place renders for")

        session.state.projects = []
        session.state.client = None
        try:
            bpy.context.scene.render.filepath = "/tmp/"
            note = scenesync.set_output(bpy.context, asset_context)
            here = bpy.context.scene.render.filepath
            check(bool(note), "the render path is set with no session (%s)" % note)
            check("assets" in here.replace("\\", "/"),
                  "under the asset tree (%s)" % here)
            check(ASSET["name"] in here,
                  "naming the asset (%s)" % here)
        finally:
            session.state.projects = kept_projects
            session.state.client = kept_client
            with properties.suspend_updates():
                props_asset.entity_type = "SHOT"

        # -- a render belongs to the file it came out of ---------------------
        # Opening another asset used to leave the previous render standing,
        # still offered for publishing - and it published the previous
        # asset's picture against the one now open.
        from BB_core.context import EntityContext as _Ctx

        other = _Ctx(project="VIL", group="Prop", entity="Kitchen-counter",
                     task="precomp3d", entity_type="asset", version=2,
                     project_id="p1", entity_id="OTHER", task_id="OTHERTASK")
        session.state.last_render = {"kind": render.IMAGE, "stream": "main",
                                     "directory": str(work_root),
                                     "stem": "Kitchen-counter_v002",
                                     "context": other, "movie": False}

        note = review.submit(bpy.context)
        check("render again" in note,
              "publishing another entity's render is refused (%s)" % note)
        check("Kitchen-counter" in note,
              "and says which render it was (%s)" % note)

        # The same render against the same entity but an older version.
        here, _src = stamp.read_current()
        if here is not None:
            older = here.at_version(max(1, (here.version or 2) - 1))
            session.state.last_render["context"] = older
            note = review.submit(bpy.context)
            check("render again" in note,
                  "and so is one from an earlier version (%s)" % note)

        # Loading a file clears it outright, which is the ordinary case.
        from BB_pipeline import handlers as bb_handlers

        session.state.last_render = {"context": other}
        bb_handlers.on_load(None)
        check(session.state.last_render is None,
              "opening a file forgets the render that came before it")

        # -- a sequence can still be published as one image ------------------
        # MP4 is for sequences. A two-frame render is a sequence by the
        # letter of it and a look by intent, and Kitsu plays a two-frame
        # movie as a flicker - so saying "send an image" has to be possible.
        pair_dir = Path(tempfile.mkdtemp(prefix="bb_pair_"))
        pair_stem = "pairtest"
        for frame in (1001, 1002):
            image = bpy.data.images.new("pair%d" % frame, 32, 18,
                                        float_buffer=True)
            image.filepath_raw = str(pair_dir / ("%s.%04d.exr" % (pair_stem, frame)))
            image.file_format = 'OPEN_EXR'
            image.save()
            bpy.data.images.remove(image)

        session.state.last_render = {"directory": str(pair_dir),
                                     "stem": pair_stem, "kind": "ANIMATION"}
        props_review = properties.get()

        check(len(review.frames_on_disk(session.state.last_render)) == 2,
              "two frames are on disk to choose between")

        props_review.review_as = "AUTO"
        made, _temp, problem = review.prepare(bpy.context,
                                              session.state.last_render)
        check(made is not None and str(made).lower().endswith(".mp4"),
              "left alone, a sequence goes as a movie (%s)" % problem or made)

        bpy.context.scene.frame_current = 1002
        props_review.review_as = "STILL"
        made, _temp, problem = review.prepare(bpy.context,
                                              session.state.last_render)
        check(made is not None and not str(made).lower().endswith(".mp4"),
              "asked for an image, it sends one (%s)" % (problem or made))
        # Which frame was chosen is asked of the chooser: the conversion
        # writes a temporary PNG with a generated name, so the frame number
        # is gone from the path by the time prepare hands it back.
        on_disk = review.frames_on_disk(session.state.last_render)
        picked = review._frame_in_hand(bpy.context, on_disk)
        check("1002" in os.path.basename(picked),
              "the frame on the playhead, not just the first (%s)"
              % os.path.basename(picked))

        bpy.context.scene.frame_current = 1
        picked = review._frame_in_hand(bpy.context, on_disk)
        check("1001" in os.path.basename(picked),
              "and the first frame when the playhead is off the render (%s)"
              % os.path.basename(picked))

        props_review.review_as = "AUTO"
        shutil.rmtree(pair_dir, ignore_errors=True)

        # -- the still format is a choice -------------------------------------------
        real = still_dir / ("%s.1002.exr" % stem)
        source = bpy.data.images.new("src", 64, 36, float_buffer=True)
        source.generated_color = (0.4, 0.2, 0.1, 1.0)
        source.filepath_raw = str(real)
        source.file_format = 'OPEN_EXR'
        source.save()
        bpy.data.images.remove(source)

        preferences.still_format = "PNG"
        made = review.convert_still(bpy.context, str(real))
        check(made is not None and made.endswith(".png"),
              "a still converts to PNG by default (%s)" % made)
        png_size = Path(made).stat().st_size if made else 0
        if made:
            Path(made).unlink()

        preferences.still_format = "JPEG"
        preferences.still_quality = 90
        made = review.convert_still(bpy.context, str(real))
        check(made is not None and made.endswith(".jpg"),
              "and to JPEG when asked (%s)" % made)
        jpeg_size = Path(made).stat().st_size if made else 0
        if made:
            Path(made).unlink()

        check(jpeg_size and png_size,
              "both formats produced a file (%d / %d bytes)" % (png_size, jpeg_size))

        session.state.last_render = {"directory": str(still_dir), "stem": stem,
                                     "context": shot_ctx}
        check(any("JPEG" in line for line in review.summary()),
              "the panel names the format it will use (%s)" % review.summary())
        preferences.still_format = "PNG"
        real.unlink()

        # -- movie names ------------------------------------------------------------
        movie_dir = render_dir.parent / "playblast"
        movie_dir.mkdir(parents=True, exist_ok=True)
        (movie_dir / ("%s1001-1005.mp4" % stem)).write_bytes(b"x")
        render.tidy_movie_name({"movie": True, "directory": str(movie_dir),
                                "stem": stem})
        check((movie_dir / ("%s.mp4" % stem)).is_file(),
              "the frame range Blender glues onto a movie is removed")
        check(not (movie_dir / ("%s1001-1005.mp4" % stem)).exists(),
              "and the mangled name is gone")

        # A still render must not be renamed as though it were a movie.
        (still_dir / ("%s.9999.exr" % stem)).write_bytes(b"")
        render.tidy_movie_name({"movie": False, "directory": str(still_dir),
                                "stem": stem})
        check((still_dir / ("%s.9999.exr" % stem)).is_file(),
              "a still render is left alone")

        session.state.last_render = None
        check("nothing has been rendered" in review.submit(bpy.context),
              "submitting with no render says so")
        session.state.client = StubClient()

        # -- Kitsu thumbnails ------------------------------------------------------
        from BB_pipeline import thumbnails
        thumbnails.clear()

        ONE_PIXEL_PNG = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
            "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

        class ThumbClient(StubClient):
            calls = []

            def thumbnail(self, preview_file_id):
                ThumbClient.calls.append(preview_file_id)
                return ONE_PIXEL_PNG

        client = ThumbClient()
        with_thumb = {"id": "e1", "name": "knife", "preview_file_id": "pf1"}
        without = {"id": "e2", "name": "man", "preview_file_id": None}

        check(thumbnails.icon_id(with_thumb) == 0, "nothing cached to begin with")
        thumbnails.fetch(client, with_thumb)
        check("pf1" in thumbnails._loaded, "a thumbnail is downloaded and loaded")

        ThumbClient.calls = []
        thumbnails.fetch(client, with_thumb)
        check(ThumbClient.calls == [],
              "a second look costs no request (preview files are immutable)")

        thumbnails.fetch(client, without)
        check(ThumbClient.calls == [],
              "an entity with no preview is never asked for")
        check(thumbnails.icon_id(without) == 0, "and reports no icon")

        thumbnails.clear()
        check(not thumbnails._loaded, "the preview collection is emptied on clear")

        # -- automatic connect ----------------------------------------------------
        autoconnect.unregister()
        autoconnect.register()
        check(bpy.app.timers.is_registered(autoconnect._try_connect),
              "the startup connect is scheduled")
        autoconnect.unregister()
        session.state.client = None
        check(autoconnect._try_connect() is None,
              "the startup connect does not reschedule itself")
        session.state.client = StubClient()

        # -- version thumbnails ------------------------------------------------
        # The browser shows one picture per version, written on save. This
        # went unnoticed for a while because the store swallowed its own
        # errors while calling an accessor that only exists on the Nuke side,
        # so what is checked here is that a thumbnail actually reaches the
        # disk - not merely that the call returned.
        from BB_pipeline import operators, prefs as bb_prefs, thumbnails
        from BB_core import workfiles as core_workfiles

        # A real 1x1 PNG, so the loader has something valid to open.
        ONE_PIXEL = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
            "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

        entity_context = fetch.current_context(bpy.context)
        check(entity_context is not None, "a context to hang a thumbnail on")

        grabbed = Path(tempfile.mkdtemp(prefix="bb_grab_")) / "grab.png"
        grabbed.write_bytes(ONE_PIXEL)

        versioned = entity_context.at_version(3)
        real_grab = operators.capture.viewport_png
        operators.capture.viewport_png = lambda context=None, **kw: str(grabbed)
        try:
            operators._store_thumb(bpy.context, versioned)
        finally:
            operators.capture.viewport_png = real_grab

        config = bb_prefs.config(bpy.context)
        expected = core_workfiles.thumb_file(versioned, "blender", 3, config)
        check(expected.is_file(),
              "saving writes the version thumbnail (%s)" % expected.name)
        check(expected.parent.name == core_workfiles.THUMB_DIR,
              "into %s beside the scene files" % core_workfiles.THUMB_DIR)

        # The browser reads it back through the same path it was written to.
        # Membership rather than a non-zero icon id, because Blender
        # allocates the id lazily and it is legitimately still 0 right after
        # a load - the same reason icon_id is never cached.
        thumbnails.version_icon(expected)
        check(any(str(expected) in key for key in thumbnails._loaded),
              "and the browser loads it into the preview collection")

        # A version nothing was saved for has no picture, and that is not an
        # error - it is what an older version legitimately looks like.
        missing = core_workfiles.thumb_file(entity_context.at_version(99),
                                            "blender", 99, config)
        check(not missing.is_file(), "a version never saved has no thumbnail")
        check(thumbnails.version_icon(missing) == 0,
              "which the browser reports as no icon rather than raising")

        # -- the scene's own output path ------------------------------------
        # Blender's output path is otherwise whatever it was last set to -
        # the startup file's /tmp, or the previous shot - so pressing F12 by
        # hand writes somewhere unrelated to what is open.
        from BB_pipeline import scenesync as bb_scenesync

        # Point at a shot explicitly: an earlier section leaves the browser
        # on an asset, and this half is about the shot layout.
        props_now = properties.get()
        with properties.suspend_updates():
            props_now.entity_type = "SHOT"
            props_now.sequence = SEQUENCE["id"]
            props_now.shot = SHOT["id"]
            props_now.task = TASK["id"]

        shot_context = fetch.current_context(bpy.context)

        # Start from somewhere else, the way a scene opened from the startup
        # file or from another shot does. An earlier section has already been
        # through the create flow, which sets this correctly.
        bpy.context.scene.render.filepath = "/tmp/"
        note = bb_scenesync.set_output(bpy.context, shot_context)
        wanted = bb_scenesync.output_path(shot_context, bpy.context)
        scene_path = bpy.context.scene.render.filepath

        check(wanted and os.path.normpath(scene_path) == os.path.normpath(wanted),
              "opening a version points the scene's output at it")
        check("sc01" in scene_path or SEQUENCE["name"] in scene_path,
              "under the sequence and shot (%s)" % scene_path)
        check(note, "and says so, rather than changing it silently")

        # Setting it twice is not a change worth reporting.
        check(not bb_scenesync.set_output(bpy.context, shot_context),
              "an output path already correct is left alone")

        # An asset has no frame range at all, so the Kitsu check has nothing
        # to say about a prop - but a prop still renders, and this is the
        # case the first attempt skipped.
        with properties.suspend_updates():
            props_now.entity_type = "ASSET"
            props_now.asset_type = ASSET_TYPE["id"]
            props_now.asset = ASSET["id"]
            props_now.task = TASK["id"]
        asset_context = fetch.current_context(bpy.context)
        if asset_context is not None:
            bb_scenesync.set_output(bpy.context, asset_context)
            asset_path = bpy.context.scene.render.filepath
            check("assets" in asset_path.replace(chr(92), "/"),
                  "a prop renders under assets/ (%s)" % asset_path)
            check(ASSET["name"] in asset_path,
                  "in its own folder (%s)" % ASSET["name"])
        with properties.suspend_updates():
            props_now.entity_type = "SHOT"

        # -- leftovers from an older build --------------------------------
        # Replacing the files does not undo an import: until 0.3.1 the core
        # went on sys.path and was imported top-level, and those entries
        # outlive an upgrade in place. Blender reads live state for its
        # policy warnings, so the old build's footprints get reported
        # against the new one until Blender restarts.
        import types as _types
        from BB_pipeline import core as bb_core_module

        addon_dir = os.path.dirname(os.path.abspath(bb_core_module.__file__))
        planted = _types.ModuleType("BB_core")
        planted.__file__ = os.path.join(addon_dir, "BB_core", "__init__.py")
        sys.modules["BB_core"] = planted
        planted_sub = _types.ModuleType("BB_core.config")
        planted_sub.__file__ = os.path.join(addon_dir, "BB_core", "config.py")
        sys.modules["BB_core.config"] = planted_sub
        sys.path.insert(0, addon_dir)

        bb_core_module._forget_older_build()

        check("BB_core" not in sys.modules and "BB_core.config" not in sys.modules,
              "a previous build's top-level modules are forgotten")
        check(addon_dir not in [os.path.abspath(entry) for entry in sys.path],
              "and the sys.path entry it added is removed")

        # A checkout's own BB_core lives outside the add-on and is shared with
        # the Nuke package and these tests; it must survive untouched.
        outside = _types.ModuleType("BB_core")
        outside.__file__ = str(REPO / "BB_core" / "__init__.py")
        sys.modules["BB_core"] = outside
        bb_core_module._forget_older_build()
        check(sys.modules.get("BB_core") is outside,
              "a checkout's own core is left alone")
        del sys.modules["BB_core"]

        # -- the browser tree -----------------------------------------------
        # The tree replaced three dropdowns, so what used to be guaranteed by
        # an enum - that you cannot reach a task belonging to another shot -
        # is now this module's job. Seeded here rather than relying on what
        # earlier sections left behind.
        from BB_pipeline import treeview

        kept = (session.state.sequences, session.state.shots, session.state.tasks)
        session.state.sequences = [
            {"id": "q1", "name": "sc01", "parent_id": "p1"},
            {"id": "q2", "name": "sc02", "parent_id": "p1"},
        ]
        session.state.shots = [
            {"id": "k1", "name": "sh01", "parent_id": "q1"},
            {"id": "k2", "name": "sh02", "parent_id": "q1"},
            {"id": "k3", "name": "sh03", "parent_id": "q2"},
        ]
        session.state.tasks = [TASK, COMP_TASK]

        props = properties.get()
        with properties.suspend_updates():
            props.entity_type = "SHOT"
            props.sequence = "q1"
            props.shot = "k1"
            props.task = TASK["id"]

        treeview.reset()
        rows = treeview.rows(props)
        check([row[0] for row in rows] == ["group", "group"],
              "a collapsed tree shows sequences only (%s)"
              % [row[0] for row in rows])

        # The bookmark restores the properties, but a tree that opens
        # collapsed hides the restored shot inside a closed branch - which
        # reads as the browser having forgotten where it was.
        treeview.reveal(props)
        revealed = treeview.rows(props)
        check(any(row[0] == "entity" and row[1] == "k1" for row in revealed),
              "opening reveals the remembered shot")
        check(any(row[0] == "task" and row[4] for row in revealed),
              "and its remembered task")
        treeview.reset()

        treeview.toggle("q1")
        rows = treeview.rows(props)
        kinds = [row[0] for row in rows]
        check(kinds.count("group") == 2, "every sequence is still listed")
        check(kinds.count("entity") == 2,
              "expanding a sequence reveals its own shots only (%d)"
              % kinds.count("entity"))

        depth_of = {row[0]: row[3] for row in rows}
        check(depth_of.get("group") == 0 and depth_of.get("entity") == 1,
              "shots are indented under their sequence")

        tasks = [row for row in rows if row[0] == "task"]
        check(len(tasks) == len(session.state.tasks) and all(r[3] == 2 for r in tasks),
              "the selected shot carries its tasks, indented (%d)" % len(tasks))

        names = [row[2] for row in rows if row[0] == "entity"]
        check(names == ["sh01", "sh02"],
              "sh03 stays under sc02 where it belongs (%s)" % names)

        selected = [row for row in rows if row[4]]
        check(any(row[0] == "entity" and row[1] == "k1" for row in selected),
              "the current shot draws as selected")

        treeview.toggle("q1")
        check(all(row[0] == "group" for row in treeview.rows(props)),
              "collapsing puts the shots away again")

        # Expanding a branch must not change the selection. A dropdown could
        # only be changed, so browsing and choosing were one act; a tree lets
        # you open a branch to look inside, and making that select the branch
        # rewrote the bookmark to a sequence the remembered shot does not
        # belong to - a pair that then failed to restore.
        treeview._pick(props, "entity", "k2")
        booked = bb_prefs.recall(bpy.context)
        treeview._pick(props, "group", "q2")
        after = bb_prefs.recall(bpy.context)
        check(after["sequence"] == booked["sequence"]
              and after["shot"] == booked["shot"],
              "expanding a branch leaves the bookmark alone (%s/%s)"
              % (after["sequence"], after["shot"]))

        # Choosing a shot in another sequence has to move both, or the pair
        # is impossible and the restore drops it.
        treeview._pick(props, "entity", "k3")
        moved = bb_prefs.recall(bpy.context)
        check(moved["sequence"] == "q2" and moved["shot"] == "k3",
              "picking across sequences moves both (%s/%s)"
              % (moved["sequence"], moved["shot"]))
        check(props.sequence == "q2" and props.shot == "k3",
              "and the selectors agree")

        # A version row reuses the same operator, so it has to reach the
        # property the Open button reads.
        session.state.workfiles = [(1, work_root / "v001.blend"),
                                   (2, work_root / "v002.blend")]
        treeview._pick(props, "version", "2")
        check(props.version == "2",
              "picking a version selects it (%s)" % props.version)

        # Put back what the later sections expect to find.
        treeview.reset()
        session.state.sequences, session.state.shots, session.state.tasks = kept
        with properties.suspend_updates():
            props.sequence = "s1"
            props.shot = "sh1"

        # -- the top bar menu ---------------------------------------------------
        from BB_pipeline import menu
        check(hasattr(_bpy.types, "BB_MT_main"), "Kitsu menu registered")
        check(menu._original_draw is not None,
              "top bar draw patched (menu sits before Help)")
        source = _bpy.types.TOPBAR_MT_editor_menus.draw.__name__
        check(source == "_patched_draw", "patched draw is installed (got %s)" % source)

        # -- changing sequence must not raise -------------------------------------
        # 'NONE' is only a legal enum value while the list is empty, so the
        # reset used to throw TypeError as soon as a sequence had shots.
        props = properties.get()
        try:
            props.sequence = "s1"
            crashed = False
        except TypeError as error:
            crashed = True
            print("      %s" % error)
        check(not crashed, "changing sequence does not raise on the reset")

    finally:
        BB_pipeline.unregister()
        check(_bpy.types.TOPBAR_MT_editor_menus.draw.__name__ != "_patched_draw",
              "top bar draw restored on unregister")
        shutil.rmtree(work_root, ignore_errors=True)

    print()
    if failures:
        print("%d FAILED:" % len(failures))
        for description in failures:
            print("  - %s" % description)
        sys.exit(1)
    print("all Blender add-on checks passed")


if __name__ == "__main__":
    main()
