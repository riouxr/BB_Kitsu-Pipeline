"""Move existing work into the per-application scene and render folders.

Scene files used to sit loose in the task folder; they now live in a folder
of their application's own (3DScenes, 2DScene), and renders in one inside
Render (3DRenders, 2DRenders). The add-ons still find files in the old
places, so nothing has to move - this tidies the ones that already exist.

    python tools/migrate_folders.py                         # dry run, every project
    python tools/migrate_folders.py --project Moon          # dry run, one project
    python tools/migrate_folders.py --project Moon --apply  # do it
    python tools/migrate_folders.py --undo <log.json>       # put it all back

Dry run by default. --apply moves the files and nothing else: no file's
contents are touched. That has a cost worth knowing - a Blender file keeping
textures or linked libraries as relative paths (//tex/wood.png) resolves them
against its own folder, so one folder deeper they go missing; and a Nuke Read
pointing at a render that moved no longer finds it. --scan lists those files
first. --fix-paths also repairs them - Blender files re-saved through the
Blender version that wrote them, Nuke scripts rewritten as text - for anyone
who would rather that than File > External Data > Find Missing Files.

Every move goes into a log that --undo replays backwards.

Close Blender, Nuke and Maya first: Windows will not move a file one of them
has open.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from BB_core import settings  # noqa: E402

BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
HELPER = REPO / "tools" / "migrate_blend_paths.py"
LOGS = Path.home() / ".BB_pipeline" / "migrations"
THUMBS = ".thumbs"


# -- what the config says --------------------------------------------------------

def app_folders(config):
    """``({ext: dcc}, {dcc: scene folder}, {dcc: render folder})``."""
    exts, scenes, renders = {}, {}, {}
    for dcc, values in config.dccs().items():
        if not isinstance(values, dict):
            continue
        ext = str(values.get("ext") or "").lower()
        if ext and values.get("scene_folder"):
            exts["." + ext] = dcc
            scenes[dcc] = values["scene_folder"]
        if values.get("render_folder"):
            renders[dcc] = values["render_folder"]
    # Maya writes two formats; both are Maya's.
    if ".ma" in exts:
        exts.setdefault(".mb", exts[".ma"])
    return exts, scenes, renders


def task_patterns(config, key):
    """Glob patterns for every task folder, read off the path templates."""
    found = []
    for kind in ("shot", "asset"):
        template = config.paths.get("%s_%s" % (key, kind)) or ""
        template = template.replace("\\", "/")
        if key == "render_dir":
            template = template.split("/Render", 1)[0]
        parts = [part for part in template.split("/") if part]
        found.append("/".join("*" if "{" in part else part for part in parts))
    return found


def project_roots(project):
    config = settings.config(project)
    roots = [config.paths.get("work_root") or "", config.paths.get("render_root") or ""]
    return config, sorted({root for root in roots if root})


# -- planning ---------------------------------------------------------------------

def plan_project(project):
    """Every move for one project, and what cannot be decided."""
    config, roots = project_roots(project)
    exts, scenes, renders = app_folders(config)
    render_names = set(renders.values())
    plan = {"project": project.get("name"), "moves": [], "render_moves": [],
            "problems": [], "roots": roots, "config": config}

    task_dcc = {}   # task folder (relative) -> set of dccs whose scenes it holds

    for root in roots:
        base = Path(root)
        if not base.is_dir():
            plan["problems"].append("root not reachable: %s" % root)
            continue
        for pattern in task_patterns(config, "work_dir"):
            for task in sorted(base.glob(pattern)):
                if not task.is_dir() or task.name in set(scenes.values()) | {"Render"}:
                    continue
                _plan_scenes(task, base, exts, scenes, plan, task_dcc)

    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        for pattern in task_patterns(config, "render_dir"):
            for task in sorted(base.glob(pattern)):
                render = task / "Render"
                if not render.is_dir():
                    continue
                _plan_renders(task, base, render, renders, render_names, plan, task_dcc)
    return plan


def _plan_scenes(task, base, exts, scenes, plan, task_dcc):
    loose = [entry for entry in task.iterdir() if entry.is_file()]
    owned = {}                                   # scene file -> dcc
    for entry in loose:
        dcc = exts.get(entry.suffix.lower())
        if dcc:
            owned[entry] = dcc
    if not owned:
        return

    key = str(task.relative_to(base)).lower()
    task_dcc.setdefault(key, set()).update(owned.values())

    stems = {entry.stem.lower(): dcc for entry, dcc in owned.items()}
    for entry in loose:
        dcc = owned.get(entry)
        if dcc is None:
            # A file that belongs to a scene - Blender's .blend1 backup, the
            # time tracker's _time.txt, Nuke's autosave - goes with it.
            name = entry.name.lower()
            dcc = next((stem_dcc for stem, stem_dcc in stems.items()
                        if name.startswith(stem + ".") or name.startswith(stem + "_")),
                       None)
        if dcc is None:
            continue
        _add_move(plan, entry, task / scenes[dcc] / entry.name)

    thumbs = task / THUMBS
    if thumbs.is_dir():
        present = set(owned.values())
        for picture in sorted(thumbs.iterdir()):
            if not picture.is_file():
                continue
            claimed = [dcc for stem, dcc in stems.items()
                       if picture.stem.lower() == stem]
            targets = claimed or (sorted(present) if len(present) == 1 else [])
            if not targets:
                plan["problems"].append("thumbnail with no scene to follow: %s" % picture)
                continue
            for dcc in sorted(set(targets)):
                _add_move(plan, picture, task / scenes[dcc] / THUMBS / picture.name,
                          copy=len(set(targets)) > 1)


def _plan_renders(task, base, render, renders, render_names, plan, task_dcc):
    old = [entry for entry in sorted(render.iterdir())
           if entry.is_dir() and entry.name not in render_names]
    if not old:
        return
    key = str(task.relative_to(base)).lower()
    dccs = {dcc for dcc in task_dcc.get(key, set()) if dcc in renders}
    if not dccs:
        # No scene files - Resolve keeps its projects in its own database -
        # so the task's name is matched against each application's
        # departments instead: a Color-Grading task is Resolve's.
        dccs = {dcc for dcc, departments in _departments(plan).items()
                if dcc in renders and task.name.lower() in departments}
    folders = {renders[dcc] for dcc in dccs}
    if len(folders) != 1:
        # Blender and Maya share 3DRenders, so both at once is no conflict.
        why = ("nothing says which application rendered it" if not folders
               else "scene files from %s both" % " and ".join(sorted(dccs)))
        plan["problems"].append("render folder left where it is (%s): %s" % (why, render))
        return
    folder = folders.pop()
    for entry in old:
        target = render / folder / entry.name
        if target.exists():
            plan["problems"].append("already there, left alone: %s" % target)
            continue
        plan["render_moves"].append((str(entry), str(target)))


def _departments(plan):
    """``{dcc: {department names as folder names}}`` for this project."""
    from BB_core import naming
    config = plan["config"]
    found = {}
    for dcc, values in config.dccs().items():
        if isinstance(values, dict):
            found[dcc] = {naming.sanitize(str(name), config).lower()
                          for name in values.get("departments") or []}
    return found


def _add_move(plan, source, target, copy=False):
    if Path(target).exists():
        plan["problems"].append("already there, left alone: %s" % target)
        return
    plan["moves"].append((str(source), str(target), bool(copy)))


# -- what the moves would break ------------------------------------------------------

_NUKE_FILE = re.compile(r'^(\s*(?:file|proxy)\s+)("?)([^"\n]+)("?)\s*$', re.M)


def scan_nuke(project_roots_, render_moves, extra_files=()):
    """Nuke scripts whose Read/Write paths point into a render folder moving."""
    olds = [(os.path.normcase(os.path.normpath(old)), new) for old, new in render_moves]
    hits = []
    seen = set()
    for script in list(extra_files) + [path for root in project_roots_
                                        if Path(root).is_dir()
                                        for path in Path(root).rglob("*.nk")]:
        script = Path(script)
        if script in seen or not script.is_file():
            continue
        seen.add(script)
        text = script.read_text(encoding="utf-8", errors="surrogateescape")
        count = 0
        for match in _NUKE_FILE.finditer(text):
            value = os.path.normcase(os.path.normpath(match.group(3).replace("/", os.sep)))
            if any(value == old or value.startswith(old + os.sep) for old, _new in olds):
                count += 1
        if count:
            hits.append((str(script), count))
    return hits


def rewrite_nuke(script, render_moves):
    """Point a script's Read/Write paths at where their renders moved."""
    text = Path(script).read_text(encoding="utf-8", errors="surrogateescape")
    changed = 0

    def swap(match):
        nonlocal changed
        raw = match.group(3)
        slash = "/" if "/" in raw else "\\"
        normalised = os.path.normcase(os.path.normpath(raw.replace("/", os.sep)))
        for old, new in render_moves:
            key = os.path.normcase(os.path.normpath(old))
            if normalised == key or normalised.startswith(key + os.sep):
                tail = os.path.normpath(raw.replace("/", os.sep))[len(os.path.normpath(old)):]
                changed += 1
                fixed = (os.path.normpath(new) + tail).replace(os.sep, slash)
                return match.group(1) + match.group(2) + fixed + match.group(4)
        return match.group(0)

    fixed = _NUKE_FILE.sub(swap, text)
    if changed:
        Path(script).write_text(fixed, encoding="utf-8", errors="surrogateescape")
    return changed


def blend_pass(files, render_moves, apply):
    """Run the Blender helper over some .blend files. Returns its report."""
    if not files:
        return []
    if not BLENDER.is_file():
        return [{"path": entry["path"], "skipped": "Blender 5.2 not found"}
                for entry in files]
    work = Path(tempfile.mkdtemp(prefix="bb_migrate_"))
    job = {"files": files, "render_moves": render_moves, "apply": apply,
           "report": str(work / "report.json")}
    (work / "job.json").write_text(json.dumps(job), encoding="utf-8")
    subprocess.run([str(BLENDER), "-b", "--factory-startup", "--python",
                    str(HELPER), "--", str(work / "job.json")],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return json.loads((work / "report.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


# -- doing it, and undoing it ---------------------------------------------------------

def apply_plan(plan, log):
    """Do the moves. Returns what could not be moved.

    One at a time, each on its own: a file some application still has open
    is reported and skipped, not the end of the run - and every move that
    did happen is already in the log, so --undo can still put it back.
    """
    failed = []
    for source, target, copy in plan["moves"]:
        try:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            if copy:
                shutil.copy2(source, target)
                log.append({"op": "copy", "to": target})
            else:
                os.replace(source, target)
                log.append({"op": "move", "from": source, "to": target})
        except OSError as error:
            failed.append("%s (%s)" % (source, error.strerror or error))
    for source, target in plan["render_moves"]:
        try:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            log.append({"op": "move", "from": source, "to": target})
        except OSError as error:
            failed.append("%s (%s)" % (source, error.strerror or error))
    return failed


def undo(log_path):
    entries = json.loads(Path(log_path).read_text(encoding="utf-8"))
    for entry in reversed(entries):
        if entry["op"] == "move":
            Path(entry["from"]).parent.mkdir(parents=True, exist_ok=True)
            os.replace(entry["to"], entry["from"])
        elif entry["op"] == "copy":
            try:
                os.remove(entry["to"])
            except OSError:
                pass
        elif entry["op"] == "text":
            Path(entry["path"]).write_text(entry["before"], encoding="utf-8",
                                           errors="surrogateescape")
    print("undone: %d step(s) from %s" % (len(entries), log_path))
    print("Blender files re-saved to fix their paths are not reverted by this;"
          " their paths were rewritten for the new folders.")


# -- the report -----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", action="append", default=[],
                        help="a project name; repeat for several (default: all)")
    parser.add_argument("--apply", action="store_true", help="do the moves")
    parser.add_argument("--scan", action="store_true",
                        help="list the files the moves would break (slow: opens every .blend)")
    parser.add_argument("--fix-paths", action="store_true",
                        help="also repair those paths - re-saves Blender files, rewrites Nuke scripts")
    parser.add_argument("--undo", metavar="LOG", help="replay a log backwards")
    arguments = parser.parse_args()

    if arguments.undo:
        undo(arguments.undo)
        return 0

    cache = settings.get("project_cache") or {}
    wanted = {name.lower() for name in arguments.project}
    projects = [p for p in sorted(cache.values(), key=lambda p: p.get("name", ""))
                if not wanted or (p.get("name") or "").lower() in wanted]

    log = []
    try:
        _run(projects, arguments, log)
    finally:
        if arguments.apply and log:
            LOGS.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            log_path = LOGS / ("migration-%s.json" % stamp)
            log_path.write_text(json.dumps(log, indent=1), encoding="utf-8")
            print('\n%d step(s) logged - undo with:  python tools/migrate_folders.py --undo "%s"'
                  % (len(log), log_path))
    if not arguments.apply:
        print("\ndry run. Nothing was changed. Add --apply to do it.")
    return 0


def _run(projects, arguments, log):
    total_moves = 0
    for project in projects:
        config, roots = project_roots(project)
        if not roots:
            continue
        plan = plan_project(project)
        if not plan["moves"] and not plan["render_moves"] and not plan["problems"]:
            continue
        total_moves += len(plan["moves"]) + len(plan["render_moves"])

        print("\n=== %s  (%s)" % (plan["project"], ", ".join(plan["roots"])))
        by_folder = {}
        for source, target, copy in plan["moves"]:
            folder = Path(target).parent.name
            by_folder[folder] = by_folder.get(folder, 0) + 1
        for folder, count in sorted(by_folder.items()):
            print("  %4d file(s) -> %s" % (count, folder))
        for source, target in plan["render_moves"]:
            print("  render %s -> %s" % (Path(source).relative_to(Path(source).parents[2]),
                                         Path(target).relative_to(Path(target).parents[3])))
        for problem in plan["problems"]:
            print("  LEFT: %s" % problem)

        blends = [{"path": source, "old_folder": None}
                  for source, _target, copy in plan["moves"]
                  if source.lower().endswith(".blend") and not copy]
        scanning = arguments.scan or arguments.fix_paths
        report = blend_pass(blends, plan["render_moves"], apply=False) if scanning else []
        relative = [r for r in report if r.get("relative")]
        refs = [r for r in report if r.get("render_refs")]
        other_versions = sorted({r.get("written_by") for r in report
                                 if r.get("written_by") and r.get("written_by") != "5.2"})
        if not scanning:
            print("  .blend files moving: %d (add --scan to list any whose paths would break)"
                  % len(blends))
        else:
            print("  .blend files moving: %d - with relative paths: %d%s - reading a moving render: %d"
              % (len(blends), len(relative),
                 (" (written by Blender %s, which would need re-saving to fix)"
                  % ", ".join(other_versions)) if other_versions and relative else "",
                 len(refs)))
        for item in relative:
            print("     relative: %s  %s" % (Path(item["path"]).name, item["relative"][:3]))

        nuke_hits = scan_nuke(plan["roots"], plan["render_moves"])
        print("  Nuke scripts reading a moving render: %d" % len(nuke_hits))
        for script, count in nuke_hits:
            print("     %s  (%d path(s))" % (Path(script).name, count))

        if arguments.apply:
            before_text = {script: Path(script).read_text(encoding="utf-8",
                                                          errors="surrogateescape")
                           for script, _count in nuke_hits}
            failed = apply_plan(plan, log)
            for problem in failed:
                print("     NOT MOVED: %s" % problem)
            if not arguments.fix_paths:
                continue
            moved_blends = [{"path": target, "old_folder": str(Path(source).parent)}
                            for source, target, copy in plan["moves"]
                            if target.lower().endswith(".blend") and not copy]
            fixed = blend_pass(moved_blends, plan["render_moves"], apply=True)
            for item in fixed:
                if item.get("fixed"):
                    print("     fixed %d path(s) in %s" % (item["fixed"], Path(item["path"]).name))
                if item.get("skipped") and (item.get("relative") or item.get("render_refs")):
                    print("     NOT FIXED %s: %s" % (Path(item["path"]).name, item["skipped"]))
            for script, _count in nuke_hits:
                # The script may itself have just moved into 2DScene.
                current = next((target for source, target, _c in plan["moves"]
                                if source == script), script)
                changed = rewrite_nuke(current, plan["render_moves"])
                if changed:
                    log.append({"op": "text", "path": current,
                                "before": before_text[script]})
                    print("     repointed %d path(s) in %s" % (changed, Path(current).name))

    print("\n%d move(s) %s." % (total_moves, "attempted" if arguments.apply else "planned"))


if __name__ == "__main__":
    sys.exit(main())
