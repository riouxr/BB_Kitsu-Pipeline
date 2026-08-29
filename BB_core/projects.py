"""What a project says about itself, kept for when the server is not there.

The roots live in a project's Kitsu brief, so finding them normally needs a
live session. A render path is wanted in places that have none - a Write made
from the Nodes menu, a .blend opened from disk in a Blender that has not
connected - and without this the roots read as unset on a show that plainly
has them.

Only the fields a Config is built from are kept, in the settings file beside
the rest of this machine's state.
"""

def _store():
    from . import settings as _settings

    kept = _settings.get("project_cache") or {}
    if not isinstance(kept, dict):
        return {}

    # The first version of this kept a single project, as a flat dict with
    # an "id" in it. Read forward rather than thrown away: a machine that
    # upgrades should not lose the one project it had.
    if kept.get("id"):
        return {kept["id"]: kept}
    return kept


def remember(project):
    """Keep what a project says about paths, so a scene needs no server.

    The roots live in a project's Kitsu brief, so finding them normally
    needs a live session. A render path is wanted in places that have none -
    a Write made from the Nodes menu, a .blend opened from disk in a Blender
    that has not connected - and without this the roots read as unset on a
    show that plainly has them.

    Kept per project rather than one at a time: a comper moves between
    shows, and remembering only the last one means every other show loses
    its roots the moment it is not the current one.

    Only the fields a Config is built from are stored, in the settings file
    beside the rest of this machine's state.
    """
    if not project or not project.get("id"):
        return

    from . import settings as _settings

    store = _store()
    store[project["id"]] = {
        "id": project["id"],
        "name": project.get("name") or "",
        "description": project.get("description") or "",
        "file_tree": project.get("file_tree"),
        "resolution": project.get("resolution"),
        "fps": project.get("fps"),
    }
    _settings.save({"project_cache": store})


def cached(project_id=None):
    """The remembered project, when it really is the one being asked about.

    Asked with an id, that project or nothing. Asked with none - a scene
    whose context carries no project - the one the browser is pointed at,
    because one show's brief holding another show's roots is worse than no
    answer: the roots resolve, they are simply the wrong ones, and nothing
    on screen says so.
    """
    from . import settings as _settings

    store = _store()
    if not store:
        return None

    if project_id:
        return store.get(project_id)

    remembered = _settings.get("last_project")
    return store.get(remembered) if remembered else None


def forget():
    """Empty the cache. For tests, and for a machine changing servers."""
    from . import settings as _settings

    _settings.save({"project_cache": {}})
