"""What a project says about itself, kept for when the server is not there.

The roots live in a project's Kitsu brief, so finding them normally needs a
live session. A render path is wanted in places that have none - a Write made
from the Nodes menu, a .blend opened from disk in a Blender that has not
connected - and without this the roots read as unset on a show that plainly
has them.

Only the fields a Config is built from are kept, in the settings file beside
the rest of this machine's state.
"""

def remember(project):
    """Keep what a project says about paths, so a Write needs no server.

    The roots live in the project's Kitsu brief, so finding them used to
    need a live session - and a Write made from the Nodes menu in a Nuke
    that has not opened the browser has none. Nothing then resolved, and
    every root read as unset on a show that plainly had them.

    Only the fields the config is built from are kept, in the settings file
    beside the rest of this machine's state.
    """
    if not project or not project.get("id"):
        return

    from . import settings as _settings

    _settings.save({"project_cache": {
        "id": project["id"],
        "name": project.get("name") or "",
        "description": project.get("description") or "",
        "file_tree": project.get("file_tree"),
        "resolution": project.get("resolution"),
        "fps": project.get("fps"),
    }})


def cached(project_id=None):
    """The remembered project, when it is the one being asked about."""
    from . import settings as _settings

    kept = _settings.get("project_cache") or {}
    if not kept.get("id"):
        return None
    if project_id and kept["id"] != project_id:
        return None
    return kept


