"""Where a host with no preferences system keeps its settings.

Blender has add-on preferences and uses them. Nuke has nothing of the sort, so
the same handful of values - which Kitsu, which login, which roots - live in a
JSON file under the user's home.

Deliberately *not* the same file as ``config.toml``. That one is the studio's
naming and layout, versioned and shared; this one is a workstation's own
answer to "which server, as whom", and belongs to the person sitting at it.

The password is not here and never will be: it goes to the credential store,
which is the same one every host and the standalone tools already share.
"""

import json
import os
from pathlib import Path

FOLDER = Path.home() / ".BB_pipeline"
FILE = FOLDER / "settings.json"
ENV_VAR = "BB_PIPELINE_SETTINGS"

DEFAULTS = {
    "server": "",
    "email": "",
    "work_root": "",
    "render_root": "",
    "allow_insecure_tls": False,
    "publish_on_save": True,
    "kitsu_normalize": False,
    "still_format": "PNG",
    # Where the browser was left, so it opens where you were.
    "last_project": "",
    "last_sequence": "",
    "last_shot": "",
    "last_task": "",
}


def path():
    """The settings file in use."""
    override = os.environ.get(ENV_VAR)
    return Path(override) if override else FILE


def load():
    """Every setting, with defaults filled in for anything absent."""
    values = dict(DEFAULTS)
    try:
        with open(path(), "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            values.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        # No file yet, or one somebody has edited into invalid JSON. Neither
        # is worth refusing to start over - the defaults are all blank.
        pass
    return values


def save(values):
    """Write settings back. Returns True when it landed."""
    current = load()
    current.update({k: v for k, v in (values or {}).items() if k in DEFAULTS})

    try:
        path().parent.mkdir(parents=True, exist_ok=True)
        with open(path(), "w", encoding="utf-8") as handle:
            json.dump(current, handle, indent=2, sort_keys=True)
        return True
    except OSError:
        return False


def get(key, default=None):
    return load().get(key, DEFAULTS.get(key, default))


def set(key, value):        # noqa: A001 - reads better than set_value here
    return save({key: value})


def config(project=None):
    """A :class:`BB_core.config.Config` for this machine, and this show.

    The same object every path in the pipeline is built from, so a host with
    no preferences system reaches the templates exactly as Blender does.

    ``project`` is a Kitsu project dict, and passing it matters: a show's
    layout and its roots can come from the project's ``file_tree`` or a
    ``[bb]`` block in its brief, and without it those are simply not read.
    Leaving it out is how a root that is set in Kitsu still reports as
    missing.
    """
    from .config import Config, load as load_config

    values = load()
    built = Config(load_config()).with_roots(
        work_root=values.get("work_root") or "",
        render_root=values.get("render_root") or "",
    )
    return built.for_kitsu_project(project) if project else built
