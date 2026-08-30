"""Where the launcher keeps its own settings - which DCC exe to run.

Deliberately not part of BB_core.settings: that file is shared with Blender
and Nuke, both of which read and write it every session for their own
reasons, and `blender_exe`/`nuke_exe` have nothing to do with either of
them - Blender and Nuke already know how they were started. Keeping them
there meant every Blender session's own settings round-trip was silently
overwriting them back to blank, for reasons that never fully surfaced even
after tracing every write site in the Blender add-on - the settings file
just is not this launcher's to share. A separate file with a single writer
(this module) sidesteps the whole class of problem rather than chasing it.
"""
import json
from pathlib import Path

FOLDER = Path.home() / ".BB_pipeline"
FILE = FOLDER / "launcher.json"

DEFAULTS = {
    "blender_exe": "",
    "nuke_exe": "",
    "resolve_exe": "",
    # A dedicated Kitsu account for bb_launch_server.py, distinct from the
    # artist's own login in BB_core/settings.json. Kitsu appears to allow
    # only one active session per person - authenticating as the artist's
    # own account here, even by resuming a saved token rather than logging
    # in fresh, was booting their browser tab's live session every time the
    # launcher made a request. A separate account has nothing to boot.
    "bot_email": "",
}


def load():
    values = dict(DEFAULTS)
    try:
        with open(FILE, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            values.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return values


def get(key, default=None):
    return load().get(key, DEFAULTS.get(key, default))


def set(key, value):        # noqa: A001 - reads better than set_value here
    current = load()
    current[key] = value
    FOLDER.mkdir(parents=True, exist_ok=True)
    with open(FILE, "w", encoding="utf-8") as handle:
        json.dump(current, handle, indent=2, sort_keys=True)
