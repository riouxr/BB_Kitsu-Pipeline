"""Read - and on request fix - the external paths inside .blend files.

Run by tools/migrate_folders.py inside a background Blender; not meant to be
run by hand:

    blender -b --factory-startup --python tools/migrate_blend_paths.py -- <job.json>

The job names each .blend with where it sits now, where it sat before the
move, and every render folder that moved. It reports for each file:

* the Blender version that wrote it,
* its relative external paths (``//tex/wood.png``) - these resolve against
  the .blend's own folder, so a file moved one folder deeper stops finding
  every one of them,
* its absolute paths into a render folder that moved.

With ``"apply": true`` in the job it also rewrites them - a relative path
re-based on the file's old folder, a render path pointed at the render's new
home - and saves, keeping the file's compression. A file written by a
different Blender than the one running this is reported and left alone:
saving would quietly upgrade it, which is not this tool's decision to make.
"""
import json
import os
import sys

import bpy

ZSTD = bytes.fromhex("28b52ffd")
GZIP = bytes.fromhex("1f8b")


def _norm(path):
    return os.path.normcase(os.path.normpath(path))


def _datablocks():
    """Every ID collection whose items can carry a filepath."""
    for name in ("images", "libraries", "sounds", "movieclips", "fonts",
                 "cache_files", "volumes"):
        for block in getattr(bpy.data, name, []):
            if hasattr(block, "filepath"):
                yield name, block


def _rebased(relative, old_folder):
    """A //relative path re-expressed from the file's old folder."""
    target = os.path.normpath(os.path.join(old_folder, relative[2:]))
    return target


def _moved_render(absolute, moves):
    """The new path for one inside a render folder that moved, or None."""
    here = _norm(absolute)
    for old, new in moves:
        prefix = _norm(old)
        if here == prefix or here.startswith(prefix + os.sep):
            return new + absolute[len(os.path.normpath(old)):]
    return None


def run(job):
    moves = [(os.path.normpath(old), os.path.normpath(new))
             for old, new in job.get("render_moves", [])]
    apply = bool(job.get("apply"))
    running = "%d.%d" % bpy.app.version[:2]
    report = []

    # The file being fixed is the backup: Blender's own .blend1 would replace
    # the artist's previous save - moved here beside it - with a copy of this
    # one from a moment ago.
    bpy.context.preferences.filepaths.save_version = 0

    for entry in job["files"]:
        path, old_folder = entry["path"], entry.get("old_folder")
        result = {"path": path, "relative": [], "render_refs": [], "fixed": 0,
                  "written_by": "", "skipped": ""}
        report.append(result)
        try:
            packed = open(path, "rb").read(4)
            # With its own window layout when it will be saved: without it,
            # the save would replace the artist's workspaces with Blender's
            # defaults.
            bpy.ops.wm.open_mainfile(filepath=path, load_ui=apply)
        except Exception as error:
            result["skipped"] = "could not open: %s" % error
            continue
        result["written_by"] = "%d.%d" % bpy.data.version[:2]

        changes = []
        for kind, block in _datablocks():
            raw = block.filepath
            if not raw or getattr(block, "packed_file", None):
                continue
            if raw.startswith("//"):
                result["relative"].append("%s:%s" % (kind, raw))
                if old_folder:
                    target = _rebased(raw, old_folder)
                    changes.append((block, bpy.path.relpath(target, start=os.path.dirname(path))))
            else:
                moved = _moved_render(bpy.path.abspath(raw), moves)
                if moved:
                    result["render_refs"].append("%s:%s" % (kind, raw))
                    changes.append((block, moved))

        # The scene's own output path is re-aimed by the add-on on every open,
        # so it is not rewritten here.

        if not apply or not changes:
            continue
        if result["written_by"] != running:
            result["skipped"] = ("written by Blender %s, not %s - saving would "
                                 "upgrade it" % (result["written_by"], running))
            continue
        for block, value in changes:
            block.filepath = value
        try:
            bpy.ops.wm.save_mainfile(filepath=path, compress=packed == ZSTD,
                                     relative_remap=False)
            result["fixed"] = len(changes)
        except Exception as error:
            result["skipped"] = "could not save: %s" % error

    return report


def main():
    job_path = sys.argv[sys.argv.index("--") + 1]
    with open(job_path, encoding="utf-8") as handle:
        job = json.load(handle)
    report = run(job)
    with open(job["report"], "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1)


main()
