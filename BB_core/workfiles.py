"""Where files live.

Path layout is template-driven the same way naming is, and both the work-file
side and the render side render their templates from the *same* context
object. That is the structural reason a render cannot end up under a different
version than the scene: nothing here accepts a version argument that did not
come off the context.
"""

import os
import re
from pathlib import Path

from . import naming, versioning
from .config import Config

# The frame number in a rendered file name: the last run of digits before
# the extension, which is where every render_file template puts it.
_FRAME = re.compile(r"(\d+)(?=\.[^.]+$)")

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


# The folder holding one small image per work version. A dot-folder beside
# the scene files rather than a parallel tree: it moves with the work when a
# shot is relocated, and sync tools already skip dot-folders by default.
THUMB_DIR = ".thumbs"


def thumb_file(context, dcc, version=None, config=None):
    """The thumbnail for one version of a scene file.

    Kitsu cannot supply this. Its preview files are numbered by their own
    revision counter, which counts publishes and review comments rather than
    work versions, so revision 3 is not version 3 and often belongs to no
    version at all - measured against a real project, not assumed. A picture
    of *this* version therefore has to be written when the version is saved.
    """
    config = (config or Config()).for_project(context.project)
    version = context.version if version is None else version
    name = "%s.png" % context.versioned(version, config)
    return work_dir(context, config) / THUMB_DIR / name


def save_thumb(context, dcc, source, version=None, config=None):
    """Store *source* as the thumbnail for a version. Returns the path or None.

    Never raises. A missing picture makes the browser show a blank slot,
    which is a far smaller problem than a save that fails because the
    thumbnail could not be written.
    """
    import shutil

    if not source or not os.path.isfile(str(source)):
        return None
    try:
        target = thumb_file(context, dcc, version, config)
        os.makedirs(str(target.parent), exist_ok=True)
        shutil.copyfile(str(source), str(target))
        return target
    except Exception:
        return None


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


def render_versions(context, stream="main", config=None):
    """Every rendered version of one stream on disk, oldest first.

    Returns ``[(version, pattern, first, last)]``, where *pattern* carries a
    printf frame placeholder rather than a real frame - the form a Read node
    or an ``ffmpeg -i`` wants, and the form that survives being handed to a
    glob.

    This exists because a render is not a work file. A comper opening a
    lighting task has nothing to *open* - there is no scene file for them -
    but there is a sequence on disk to read, and Kitsu cannot hand it over:
    what Kitsu stores is the review movie, re-encoded to H.264, which is not
    what anybody comps against.
    """
    config = (config or Config()).for_project(context.project)
    if stream not in config.streams:
        return []

    # render_dir ends in the version folder, so its parent is the folder that
    # holds every version of this stream.
    try:
        folder = render_dir(context.at_version(1), stream, config).parent
    except (ValueError, KeyError):
        return []
    if not folder.is_dir():
        return []

    wanted = naming.format_base(context.as_fields(), config)
    ext = config.streams[stream].get("ext", "exr")

    found = []
    for entry in sorted(folder.iterdir()):
        if not entry.is_dir():
            continue
        # Same rule as existing_versions: the folder has to name this shot
        # and this task, so a stray folder cannot invent a version.
        parsed = naming.parse(entry.name, config)
        if not parsed:
            continue
        if naming.format_base(parsed, config).lower() != wanted.lower():
            continue
        version = parsed["version"]
        if version is None:
            continue
        frames = sorted(entry.glob("*.%s" % ext))
        if not frames:
            continue
        pattern, first, last = _frame_pattern(frames)
        if pattern:
            found.append((version, pattern, first, last))

    return sorted(found)


def _frame_pattern(frames):
    """``(pattern, first, last)`` for a run of numbered frames."""
    numbers = []
    pattern = ""
    for path in frames:
        match = _FRAME.search(path.name)
        if not match:
            continue
        numbers.append(int(match.group(1)))
        if not pattern:
            digits = len(match.group(1))
            pattern = str(path.parent / (
                path.name[:match.start(1)]
                + "%0{}d".format(digits)
                + path.name[match.end(1):]))

    if not numbers:
        return "", 0, 0
    return pattern, min(numbers), max(numbers)


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
