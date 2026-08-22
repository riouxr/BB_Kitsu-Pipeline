"""Configuration loading for the BB Kitsu pipeline.

Naming templates, path layouts and per-stream output rules are data, not code.
This module loads ``presets/default.toml`` and merges any studio or user
override on top, so moving a render root or renaming a task folder is an edit
to a config file rather than a hunt through five DCC integrations.

Override resolution, first hit wins:

  1. an explicit path passed to :func:`load`
  2. the ``BB_PIPELINE_CONFIG`` environment variable
  3. ``~/.BB_pipeline/config.toml``

Overrides are merged per key, so a file that only sets ``paths.render_root``
keeps every default around it.
"""

import copy
import os
import tomllib
from pathlib import Path

DEFAULT_PRESET = Path(__file__).parent / "presets" / "default.toml"
USER_CONFIG = Path.home() / ".BB_pipeline" / "config.toml"
ENV_VAR = "BB_PIPELINE_CONFIG"

_cache = {}


def _read_toml(path):
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def _merge(base, override):
    """Recursive dict merge; ``override`` wins on scalars, tables combine."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def override_path(explicit=None):
    """The override file that would be used, or None if there is not one."""
    for candidate in (explicit, os.environ.get(ENV_VAR), USER_CONFIG):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def load(explicit=None, refresh=False):
    """The merged configuration dict.

    Cached per override path, because the browser asks for it on every redraw
    and re-reading two TOML files at UI rate is pointless. Pass
    ``refresh=True`` after editing a config file in place.
    """
    override = override_path(explicit)
    key = str(override) if override else ""

    if refresh:
        _cache.pop(key, None)
    if key in _cache:
        return _cache[key]

    config = _read_toml(DEFAULT_PRESET)
    if override:
        config = _merge(config, _read_toml(override))

    _cache[key] = config
    return config


def clear_cache():
    _cache.clear()


class Config:
    """Thin accessor over the merged dict.

    Exists so callers read ``config.naming["base"]`` instead of threading a
    bare dict through every function, and so runtime values that are not in
    any file - the roots the add-on preferences supply - can be layered on
    without writing them to disk.
    """

    def __init__(self, data=None, **runtime):
        self._data = copy.deepcopy(data if data is not None else load())
        if runtime:
            self._data = _merge(self._data, {"paths": runtime})

    @property
    def data(self):
        return self._data

    @property
    def naming(self):
        return self._data["naming"]

    @property
    def paths(self):
        return self._data["paths"]

    @property
    def streams(self):
        return self._data.get("streams", {})

    def dcc(self, name):
        """Per-DCC settings, e.g. the work file extension."""
        return self._data.get("dcc", {}).get(name, {})

    def with_roots(self, work_root=None, render_root=None):
        """A copy carrying the roots the host application knows about."""
        runtime = {}
        if work_root:
            runtime["work_root"] = str(work_root)
        if render_root:
            runtime["render_root"] = str(render_root)
        return Config(self._data, **runtime)

    def for_kitsu_project(self, project):
        """A copy using whatever the Kitsu project says about layout.

        Two sources, in order of how deliberate they are:

        1. the project's ``file_tree`` - the studio's layout, set through the
           API, describing where working files go;
        2. a ``[bb]`` block in the project's brief - a plain text box any
           producer can edit in the browser, which is the only place a root
           can be set without a developer. It goes last because it is the
           most specific and the most manual.

        Kitsu wins over the local config where it speaks, because every DCC
        should read the same answer. A project that says nothing - the normal
        case - changes nothing.
        """
        from . import brief, filetree

        data = self._data
        changed = False

        try:
            overrides = filetree.translate(project)
        except filetree.UnsupportedTree:
            overrides = None
        if overrides:
            data = _merge(data, overrides)
            changed = True

        try:
            from_brief = brief.parse((project or {}).get("description"))
        except brief.BadBrief:
            from_brief = None
        if from_brief:
            data = _merge(data, from_brief)
            changed = True

        return Config(data) if changed else self

    def for_project(self, project):
        """A copy with any ``[projects.<name>]`` overrides folded into paths.

        Roots are per-project in practice - one show lives on a different
        drive to the next - but most of them differ only by a folder under a
        common root, which the ``{project}`` token in the path templates
        already covers. This is for the ones that genuinely need their own
        root, or their own layout underneath it.

        Matched case-insensitively, because the name comes from Kitsu and
        nobody wants a path silently falling back to the default over a
        capital letter.
        """
        if not project:
            return self

        projects = self._data.get("projects") or {}
        for name, overrides in projects.items():
            if name.lower() == str(project).lower() and overrides:
                return Config(_merge(self._data, {"paths": overrides}))
        return self
