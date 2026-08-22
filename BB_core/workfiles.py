"""Where files live.

Path layout is template-driven the same way naming is, and both the work-file
side and the render side render their templates from the *same* context
object. That is the structural reason a render cannot end up under a different
version than the scene: nothing here accepts a version argument that did not
come off the context.
"""

import re
from pathlib import Path

from . import naming, versioning
from .config import Config

_TOKEN = re.compile(r"\{(\w+)\}")


class RootNotConfigured(Exception):
    """Raised rather than guessing when a root is blank in the config."""


def _root(config, key):
    value = (config.paths.get(key) or "").strip()
    if not value:
        raise RootNotConfigured(
            "paths.%s is not set - configure it in the add-on preferences "
            "or in your config.toml" % key
        )
    return Path(value)


def _fill(template, context, config, **extra):
    """Render a path template from a context.

    Field values are sanitized exactly as the naming module does them, so a
    folder called ``FF9-0070`` and a file called ``..._FF9-0070_...`` cannot
    disagree about how a name with a space in it was cleaned up.
    """
    values = {name: naming.sanitize(value, config)
              for name, value in context.as_fields().items()}
    values.update(extra)

    missing = [name for name in _TOKEN.findall(template) if not values.get(name)]
    if missing:
        raise ValueError("path template %r is missing %s" % (template, ", ".join(missing)))

    return template.format(**values)


# ── Work files ────────────────────────────────────────────────────────────────

def _template(config, key, context):
    """A path template for this context's entity type.

    Shots and assets get their own layout key, so the two trees cannot land in
    the same folder because a sequence happened to share a name with an asset
    type.
    """
    name = "%s_%s" % (key, context.entity_type)
    if name not in config.paths:
        raise ValueError("no %s template configured for %r entities"
                         % (key, context.entity_type))
    return config.paths[name]


def work_dir(context, config=None):
    """The folder holding every version of one entity/task's scene files."""
    config = (config or Config()).for_project(context.project)
    return _root(config, "work_root") / _fill(
        _template(config, "work_dir", context), context, config)


def work_file(context, dcc, version=None, config=None):
    """The scene file path for a given version of a context."""
    config = (config or Config()).for_project(context.project)
    version = context.version if version is None else version
    ext = config.dcc(dcc).get("ext", "")
    if not ext:
        raise ValueError("no work file extension configured for dcc %r" % dcc)

    name = _fill(
        config.paths["work_file"], context, config,
        versioned=context.versioned(version, config),
        ext=ext,
    )
    return work_dir(context, config) / name


def list_workfiles(context, dcc, config=None):
    """Every existing version for this context, as ``(version, Path)``.

    This is what the browser lists. An empty list is the signal that the
    entity has never been worked on, which is what the "create" path keys off.
    """
    config = config or Config()
    ext = config.dcc(dcc).get("ext", "")
    return versioning.existing_versions(
        work_dir(context, config), context.as_fields(), ext, config
    )


def next_workfile(context, dcc, config=None):
    """``(Path, version)`` for the next scene file to create.

    Returns v001 for a shot with nothing on disk, so "open" and "create new"
    are the same code path with a different starting point.
    """
    config = config or Config()
    ext = config.dcc(dcc).get("ext", "")
    version = versioning.next_version(
        work_dir(context, config), context.as_fields(), ext, config
    )
    return work_file(context, dcc, version, config), version


# ── Renders ───────────────────────────────────────────────────────────────────

def render_dir(context, stream="main", config=None):
    """The version folder one output stream renders into.

    Every stream of a version sits under the same context, differing only by
    the stream folder - which is what makes main/proxy/offline/matte/plate
    derivable from one render instead of five separate setups.
    """
    config = (config or Config()).for_project(context.project)
    if stream not in config.streams:
        raise ValueError("unknown stream %r; configured: %s"
                         % (stream, ", ".join(sorted(config.streams))))

    folder = config.streams[stream].get("folder", stream)
    return _root(config, "render_root") / _fill(
        _template(config, "render_dir", context), context, config,
        stream=folder,
        versioned=context.versioned(config=config),
    )


def render_file(context, stream="main", frame="####", config=None):
    """The frame-numbered output path for one stream of this context."""
    config = (config or Config()).for_project(context.project)
    settings = config.streams[stream]
    name = _fill(
        config.paths["render_file"], context, config,
        versioned=context.versioned(config=config),
        frame=frame,
        ext=settings.get("ext", "exr"),
    )
    return render_dir(context, stream, config) / name
