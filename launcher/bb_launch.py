"""Double-click launcher: open a DCC on the latest work file for a Kitsu task.

Standalone on purpose - this runs outside Blender and outside Nuke, started
cold by Windows from a registered ``bbopen://`` URL (see ``install.py`` in
this folder), so it cannot rely on either host's Python or its running state.
It only needs ``BB_core`` and a plain system Python 3.11+.

Usage, for testing from a terminal without the protocol handler::

    python bb_launch.py <task_id>
    python bb_launch.py "bbopen://open?task_id=<task_id>"
    python bb_launch.py "bbopen://open?task_id=<task_id>&version=2"

``version``, when given, is Kitsu's *preview* revision number from the task
panel - not necessarily the local work file's version number. The two only
line up when every publish bumps both together, which is this pipeline's own
convention but not something Kitsu enforces, so it is opened as an exact
match against what is on disk rather than assumed. Omitted, the latest local
version opens instead, same as opening a task from the Blender/Nuke browser.

Resolution mirrors what the Blender and Nuke browsers do once a task is
selected (see ``blender/BB_pipeline/fetch.py:current_context`` and
``nuke/BB_pipeline_nuke/fetch.py:current_context``), except every level is
looked up directly by id instead of coming out of a browser's cached lists -
a cold launch has no cache to draw on.
"""
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import blender_create
import launcher_config
import resolve_launch
from BB_core import credentials, settings, versioning, workfiles
from BB_core.context import EntityContext
from BB_core.kitsu import KitsuClient, KitsuError, explain
from BB_core.workfiles import RootNotConfigured

# Which local setting names the executable for each dcc.
_EXE_SETTING = {"blender": "blender_exe", "nuke": "nuke_exe", "resolve": "resolve_exe"}

_TASK_ID = re.compile(r"task_id=([0-9a-fA-F-]+)")


def parse_arg(arg):
    """``(task_id, version)`` out of a raw CLI argument - a bare id or a URL.

    ``version`` is None when the argument carries none, which is the normal
    case: only the task panel's explicit Launch button sends one.
    """
    match = _TASK_ID.search(arg)
    if not match:
        return arg.strip(), None

    query = parse_qs(urlsplit(arg).query)
    version = (query.get("version") or [None])[0]
    return match.group(1), (int(version) if version else None)


def _find(items, item_id):
    return next((item for item in (items or []) if item.get("id") == item_id), None)


def resolve_context(client, task_id):
    """An :class:`EntityContext` built entirely from one task id.

    Five or six small requests, all cheap (a few tens of ms each against a
    studio server per the timings noted elsewhere in this pipeline) - a cold
    launch happens once per double-click, not on every redraw, so there is
    no cache to keep warm here.
    """
    task = client.task(task_id)
    if not task:
        raise KitsuError("no such task: %s" % task_id)

    project_id = task.get("project_id")
    entity_id = task.get("entity_id")
    task_type_id = task.get("task_type_id")

    project = client.project(project_id)
    if not project:
        raise KitsuError("no such project: %s" % project_id)

    # A task's entity is either a shot or an asset; nothing on the task
    # itself says which, so try the shot route first and fall back.
    shot = None
    try:
        shot = client.shot(entity_id)
    except KitsuError:
        shot = None
    is_asset = not shot

    entity = shot if shot else client.asset(entity_id)
    if not entity:
        raise KitsuError("no such shot/asset: %s" % entity_id)

    if is_asset:
        group = _find(client.asset_types(project_id), entity.get("entity_type_id"))
    else:
        group = _find(client.sequences(project_id), entity.get("parent_id"))
    if not group:
        raise KitsuError(
            "could not resolve the %s's parent group"
            % ("asset type" if is_asset else "sequence"))

    task_type = _find(client.task_types(), task_type_id)
    department = _find(client.departments(), (task_type or {}).get("department_id"))

    return EntityContext(
        entity_type="asset" if is_asset else "shot",
        project=project.get("code") or project.get("name", ""),
        group=group.get("name", ""),
        entity=entity.get("name", ""),
        task=(task_type or {}).get("name", ""),
        project_id=project["id"],
        group_id=group["id"],
        entity_id=entity["id"],
        task_id=task["id"],
        task_type_id=task_type_id or "",
        department=(department or {}).get("name", ""),
        version=0,
        server=client.host,
    )


def dcc_for(context, config):
    """Which DCC owns this task's department, per ``[dcc.*]`` in the config."""
    department = (context.department or "").lower()
    for name in _EXE_SETTING:
        departments = config.dcc(name).get("departments")
        if departments and department in {d.lower() for d in departments}:
            return name
    raise KitsuError(
        "no DCC configured for department %r" % context.department)


def launch(dcc, exe, scene_path):
    if not exe:
        raise KitsuError(
            "no %s executable configured - run: python dcc_versions.py set %s <path>"
            % (dcc, dcc))
    subprocess.Popen([exe, str(scene_path)])


def list_versions(context, config, dcc):
    """Every version of this task's work as ``(version, identifier)``, newest
    first - a ``Path`` for a file-based DCC, a Resolve project name for
    Resolve. One shape either way, so a caller does not need to know which
    kind of DCC it is asking about.
    """
    if dcc == "resolve":
        return resolve_launch.resolve_versions(context)
    work_dir = workfiles.work_dir(context, config)
    ext = config.dcc(dcc).get("ext", "")
    found = versioning.existing_versions(work_dir, context.as_fields(), ext, config)
    return sorted(found, reverse=True)


def open_version(context, config, dcc, wanted_version=None):
    """Opens *wanted_version* of this task in *dcc*, or the latest.

    Returns ``(version, identifier)``. Raises :class:`KitsuError` with a
    message meant to reach whoever asked - the CLI's stdout, or the
    browser's Launch button by way of ``bb_launch_server.py`` - not just a
    developer reading a traceback.
    """
    exe = launcher_config.get(_EXE_SETTING[dcc])

    # Resolve has to be connected *before* asking it what versions exist -
    # listing versions means reading its own project database
    # (list_versions -> resolve_versions), which answers "nothing found"
    # rather than raising when Resolve is not reachable yet. Doing this in
    # the other order silently reported "no work yet" for a task that had
    # two real versions, just because Resolve happened to be closed.
    if dcc == "resolve" and not resolve_launch.ensure_running(exe):
        raise KitsuError(
            "could not reach Resolve - is it installed, and is %r set? "
            "(python dcc_versions.py set resolve <path>)" % _EXE_SETTING[dcc])

    versions = list_versions(context, config, dcc)

    if wanted_version:
        match = next((v for v in versions if v[0] == wanted_version), None)
        if not match:
            raise KitsuError(
                "no v%03d for %s / %s / %s - versions found: %s"
                % (wanted_version, context.group, context.entity, context.task,
                   ", ".join("v%03d" % v for v, _identifier in versions) or "none"))
    elif not versions and dcc == "blender":
        # Blender is the one DCC where "nothing exists yet" doesn't have to
        # be a dead end: a fresh startup scene is a real, useful first
        # version, so Launch creates and stamps it (the same logic
        # `bpy.ops.bb.new_workfile` uses, see blender_create_bg.py) rather
        # than sending the artist to do it by hand first.
        path, version = workfiles.next_workfile(context, dcc, config)
        try:
            blender_create.create(exe, context.at_version(version), path)
        except RuntimeError as error:
            raise KitsuError(str(error))
        match = (version, path)
    else:
        if not versions:
            raise KitsuError(
                "no work yet for %s / %s / %s - create one from %s first"
                % (context.group, context.entity, context.task, dcc))
        match = versions[0]

    version, identifier = match

    if dcc == "resolve":
        try:
            resolve_launch.open_project(identifier)
        except RuntimeError as error:
            raise KitsuError(str(error))
    else:
        launch(dcc, exe, identifier)

    return version, identifier


def main(argv):
    if len(argv) < 2:
        print("usage: bb_launch.py <task_id or bbopen:// url>")
        return 1

    task_id, wanted_version = parse_arg(argv[1])

    values = settings.load()
    server, email = values.get("server"), values.get("email")
    if not server or not email:
        print("Kitsu server/email not set - run the Blender or Nuke add-on "
              "once to configure BB_pipeline settings")
        return 1

    password = credentials.get_password(email)
    if not password:
        print("no stored password for %s - log in once from Blender or Nuke "
              "with 'remember password' on" % email)
        return 1

    client = KitsuClient(server, verify=not values.get("allow_insecure_tls"))
    try:
        client.log_in(email, password)
        context = resolve_context(client, task_id)

        config = settings.config(client.project(context.project_id))
        dcc = dcc_for(context, config)

        version, identifier = open_version(context, config, dcc, wanted_version)
        print("opening %s in %s (v%03d)" % (identifier, dcc, version))
        return 0
    except KitsuError as error:
        print(explain(error, server, email))
        return 1
    except RootNotConfigured as error:
        print(error)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
