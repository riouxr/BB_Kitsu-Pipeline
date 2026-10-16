"""Where files live.

Path layout is template-driven the same way naming is, and both the work-file
side and the render side render their templates from the *same* context
object. That is the structural reason a render cannot end up under a different
version than the scene: nothing here accepts a version argument that did not
come off the context.
"""

import copy
import os
import re
from pathlib import Path

from . import naming, versioning, volumes
from .config import Config

# The frame number in a rendered file name: the last run of digits before
# the extension, which is where every render_file template puts it.
_FRAME = re.compile(r"(\d+)(?=\.[^.]+$)")

# A version folder: ``v003``, and nothing else.
_VERSION_FOLDER = re.compile(r"^[vV](\d+)$")

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

    # One brief serves the whole studio, so the root it carries is spelled
    # for whichever platform wrote it. This is the only place a root becomes
    # a real path, so it is the only place that has to know.
    missing = volumes.unresolved(value)
    if missing:
        raise RootNotConfigured(
            "paths.%s is %s, and this machine has no mapping for %s - add one "
            "under \"volumes\" in the settings file, for example "
            "{\"%s\": \"/Volumes/YourDisk\"}"
            % (key, value, missing, missing)
        )

    return Path(volumes.localise(value))


def _fill(template, context, config, **extra):
    """Render a path template from a context.

    Field values are sanitized exactly as the naming module does them, so a
    folder called ``FF9-0070`` and a file called ``..._FF9-0070_...`` cannot
    disagree about how a name with a space in it was cleaned up.
    """
    values = {name: naming.sanitize(value, config)
              for name, value in context.as_fields().items()}
    values.update(extra)

    # A value handed in explicitly may legitimately be empty - a stream with
    # no folder of its own is the reason - so only a token with nothing behind
    # it at all, or an empty field off the context, counts as missing.
    missing = [name for name in _TOKEN.findall(template)
               if name not in values
               or (not values.get(name) and name not in extra)]
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


def scene_folder(config, dcc):
    """The folder one application keeps its scene files in, or ''."""
    return str(config.dcc(dcc).get("scene_folder") or "") if dcc else ""


def scene_folders(config):
    """Every application's scene folder, for recognising one in a path."""
    return sorted({str(settings.get("scene_folder"))
                   for settings in config.dccs().values()
                   if isinstance(settings, dict) and settings.get("scene_folder")})


def scene_dir(context, dcc, config=None):
    """Where one application's scene files for this entity and task live.

    A folder of the application's own inside the task folder - 3DScenes,
    2DScene - rather than the task folder itself, where a Blender scene, a
    Nuke script, a render folder and a thumbnail cache all used to sit side
    by side. An application with no scene_folder configured keeps the task
    folder, which is also where every file from before this lives.
    """
    config = (config or Config()).for_project(context.project)
    base = work_dir(context, config)
    folder = scene_folder(config, dcc)
    return base / folder if folder else base


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
    return scene_dir(context, dcc, config) / name


def list_workfiles(context, dcc, config=None):
    """Every existing version for this context, as ``(version, Path)``.

    This is what the browser lists. An empty list is the signal that the
    entity has never been worked on, which is what the "create" path keys off.
    """
    config = config or Config()
    ext = config.dcc(dcc).get("ext", "")
    fields = context.as_fields()
    here = scene_dir(context, dcc, config)
    found = versioning.existing_versions(here, fields, ext, config)

    # Scene files from before each application had a folder of its own sat
    # in the task folder itself. Still listed, so a task that has not been
    # moved does not appear to have lost its work, and still counted, so the
    # next version carries on from v008 rather than starting a second v001
    # in the new folder.
    legacy = work_dir(context, config)
    if legacy != here:
        known = {version for version, _path in found}
        found = found + [(version, path) for version, path in
                         versioning.existing_versions(legacy, fields, ext, config)
                         if version not in known]
    return sorted(found, key=lambda item: item[0])


# ── Applications doing the same work ─────────────────────────────────────────
#
# Nuke and JOS Compositor are both compositors, and a shot's comp can move from
# one to the other. ``shares_versions_with`` under ``[dcc.<name>]`` says so,
# and then the two count one version sequence between them: sh01_v003.nk is
# followed by sh01_v004.joscomp, never by a second v003. Either side naming
# the other is enough - one sequence cannot be shared in one direction only,
# or the side that did not look would hand the same number out again.

def sharing_dccs(config, dcc):
    """*dcc* first, then every application sharing its version sequence."""
    config = config or Config()
    every = config.dccs()
    mine = {str(name) for name in
            ((every.get(dcc) or {}).get("shares_versions_with") or [])}
    found = [dcc]
    for name in sorted(every):
        settings = every[name]
        if name == dcc or not isinstance(settings, dict) or not settings.get("ext"):
            continue
        theirs = {str(other) for other in (settings.get("shares_versions_with") or [])}
        if name in mine or dcc in theirs:
            found.append(name)
    return found


def workfile_dcc(path, config=None, among=None):
    """The application a work file belongs to, from its extension, or ''.

    *among* limits the answer to those applications (a sharing group), so
    an extension two unrelated applications both use cannot be misread.
    """
    config = config or Config()
    suffix = Path(str(path)).suffix.lstrip(".").lower()
    if not suffix:
        return ""
    names = list(among) if among else sorted(config.dccs())
    for name in names:
        if str(config.dcc(name).get("ext") or "").lower() == suffix:
            return name
    return ""


def list_shared_workfiles(context, dcc, config=None):
    """Every version of this task's work in *dcc* and the applications
    sharing its versions, as ``(version, Path, owner)``, oldest first.

    For a studio where nothing is shared this is ``list_workfiles`` with the
    application's name added. When two files carry one number (written
    before the sequence was shared), both are listed, *dcc*'s own first.
    """
    config = config or Config()
    group = sharing_dccs(config, dcc)
    found = []
    for name in group:
        for version, path in list_workfiles(context, name, config):
            found.append((version, path, name))
    return sorted(found, key=lambda item: (item[0], group.index(item[2])))


def find_workfile(context, dcc, version=None, config=None):
    """``(Path, owner)`` for one version of the shared work, or ``(None, '')``.

    What maps a render's version back to the scene that made it, whichever
    application that was: the comp's renders share one 2DRenders folder.
    """
    version = context.version if version is None else version
    for found, path, owner in list_shared_workfiles(context, dcc, config):
        if found == version:
            return path, owner
    return None, ""


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
    wanted = scene_dir(context, dcc, config) / THUMB_DIR / name
    if not wanted.exists():
        # A version from before the scene folders keeps its picture where it
        # was taken, beside it in the task folder.
        legacy = work_dir(context, config) / THUMB_DIR / name
        if legacy.exists():
            return legacy
    return wanted


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
    are the same code path with a different starting point. Counts the
    versions of every application sharing *dcc*'s sequence (``sharing_dccs``),
    so a JOS project saved after sh01_v003.nk is v004.
    """
    found = list_shared_workfiles(context, dcc, config)
    # One past the highest wherever it sits - old task folder or new scene
    # folder - never a count, so deleting v002 cannot hand out v003 twice.
    version = (found[-1][0] + 1) if found else 1
    return work_file(context, dcc, version, config), version


# ── Renders ───────────────────────────────────────────────────────────────────

def render_folder(config, dcc):
    """The folder one application renders into, or ''."""
    return str(config.dcc(dcc).get("render_folder") or "") if dcc else ""


def render_folders(config):
    """Every application's render folder, and '' for renders made before any.

    What a reader walks when it does not know - or care - which application
    made a render: a comp reading the lighting renders, a viewer after the
    newest frames whatever made them.
    """
    names = sorted({str(settings.get("render_folder"))
                    for settings in config.dccs().values()
                    if isinstance(settings, dict) and settings.get("render_folder")})
    return names + [""]


def render_dir(context, stream="main", config=None, dcc=None):
    """The version folder one output stream renders into.

    Every stream of a version sits under the same context, differing only by
    the stream folder - which is what makes main/proxy/offline/matte/plate
    derivable from one render instead of five separate setups.

    ``dcc`` is the application rendering, and picks its render folder. With
    none the render is filed as renders were before the folders existed -
    which is also how a reader finds those.
    """
    config = (config or Config()).for_project(context.project)
    return _render_dir(context, stream, config, render_folder(config, dcc))


def find_render_dir(context, stream="main", config=None):
    """The version folder that actually holds this version's frames.

    For a reader that knows the version but not which application rendered
    it - an import, the Set Master button - each render folder is tried in
    turn, and the pre-folder location last. When none exists the answer is
    the pre-folder location, so a caller reporting "nothing there" names a
    real path.
    """
    config = (config or Config()).for_project(context.project)
    for renders in render_folders(config):
        folder = _render_dir(context, stream, config, renders)
        if folder.is_dir():
            return folder
    return _render_dir(context, stream, config, "")


def _render_dir(context, stream, config, renders):
    config = (config or Config()).for_project(context.project)
    if stream not in config.streams:
        raise ValueError("unknown stream %r; configured: %s"
                         % (stream, ", ".join(sorted(config.streams))))

    folder = config.streams[stream].get("folder", stream)
    filled = _fill(
        _template(config, "render_dir", context), context, config,
        stream=folder,
        renders=renders,
        version=naming.format_version(context.version or 1, config),
        versioned=context.versioned(config=config),
    )
    # A stream with no folder of its own leaves an empty segment behind -
    # `main` deliberately has none, because a directory called `main` next to
    # nothing else says nothing.
    filled = "/".join(part for part in filled.replace("\\", "/").split("/") if part)
    return _root(config, "render_root") / filled


def render_stem(context, config=None):
    """What a rendered file is called, before the frame and the extension.

    Its own function because the scene's output path, the render operators
    and the listing all have to agree on it, and three copies of one string
    is how they stop agreeing.
    """
    config = (config or Config()).for_project(context.project)

    # A template of its own for each entity type when one is set, and the
    # plain one otherwise. A shot's renders travel far from the folders that
    # name their show - into an edit, a review, somebody else's comp - and a
    # drive full of sh010_v003 from six different shows is how nobody can
    # say where a frame came from. An asset's rarely leave.
    template = (config.paths.get("render_stem_%s" % context.entity_type)
                or config.paths["render_stem"])

    extra = {"version": naming.format_version(context.version or 1, config),
             "versioned": context.versioned(config=config)}
    # A file never stamped gets its context from its path, which starts at
    # the project root and so never names the show. The config was built
    # for one, and that is the answer.
    if not context.project and config.project_name:
        extra["project"] = naming.sanitize(config.project_name, config)

    try:
        return _fill(template, context, config, **extra)
    except ValueError:
        if template == config.paths["render_stem"]:
            raise
        # Nothing anywhere names what the longer name needs. A render filed
        # under the short name is still a render; one that cannot be named
        # at all is not rendered, which is how rendering an unstamped shot
        # came to stop outright.
        return _fill(config.paths["render_stem"], context, config, **extra)


def render_output(context, stream="main", config=None, dcc=None):
    """Where a host application should point its own output, as a prefix.

    The version folder plus the version's name and a trailing dot, so a host
    that appends the frame and the extension itself - which Blender does -
    lands on the same ``name_v003.0001.exr`` the pipeline's own render
    operators produce, and the review panel can find either.

    Kept here rather than in the add-on because the whole point is that the
    scene's output path and the pipeline's render path are one answer, not
    two that have to be kept in step.
    """
    config = (config or Config()).for_project(context.project)
    return render_dir(context, stream, config, dcc) / (render_stem(context, config) + ".")


def render_versions(context, stream="main", config=None, dcc=None):
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

    fields = context.as_fields()
    ext = config.streams[stream].get("ext", "exr")

    # One application's folder when asked for; otherwise every one there is,
    # and the folder renders went into before any of them existed.
    wanted = [render_folder(config, dcc)] if dcc else render_folders(config)

    found = []
    seen = set()
    for renders in wanted:
        # render_dir ends in the version folder, so its parent is the folder
        # that holds every version of this stream.
        try:
            folder = _render_dir(context.at_version(1), stream, config,
                                 renders).parent
        except (ValueError, KeyError):
            continue
        if folder in seen or not folder.is_dir():
            continue
        seen.add(folder)

        for entry in sorted(folder.iterdir()):
            if not entry.is_dir():
                continue
            version = _version_folder(entry.name, fields, config)
            if version is None:
                continue
            frames = sorted(entry.glob("*.%s" % ext))
            if not frames:
                continue
            pattern, first, last = _frame_pattern(frames)
            if pattern:
                found.append((version, pattern, first, last))

    return sorted(found)


def every_render_version(context, config=None):
    """Every rendered version of every stream on disk, oldest first.

    Returns ``[(version, stream, pattern, first, last)]``. For a browser
    listing another task's frames: a playblast is a stream of its own, in a
    folder of its own, so a reader asking only for ``main`` never sees one -
    which is how an animator's playblasts stayed invisible to a colourist.
    """
    config = (config or Config()).for_project(context.project)
    found = []
    for stream in sorted(config.streams):
        for version, pattern, first, last in render_versions(context, stream, config):
            found.append((version, stream, pattern, first, last))
    return sorted(found)


def _version_folder(name, fields, config):
    """The version a render folder holds, or None if it holds none.

    Two shapes are accepted. The current one is just ``v003``: the folders
    above it already say which project, entity and task this is, so the
    version folder repeating all of it was noise. The older one spelled the
    whole name out, and is still read so renders made before the layout
    changed keep showing up in the browser.
    """
    match = _VERSION_FOLDER.match(name)
    if match:
        return int(match.group(1))

    parsed = naming.parse(name, config)
    if parsed and parsed.get("version") is not None:
        if naming.names_the_same(parsed, fields, config):
            return parsed["version"]
        return None

    # Folders written before the name stopped repeating the context. Parsed
    # against the scheme that made them, not the one in force now, or a show
    # that predates the change loses every render it has.
    return _legacy_version_folder(name, config)


# What a name looked like when it spelled the whole context out.
_LEGACY_BASE = "{project}_{group}_{entity}_{task}"


def _legacy_version_folder(name, config):
    legacy = copy.deepcopy(config)
    legacy.naming["base"] = _LEGACY_BASE
    parsed = naming.parse(name, legacy)
    if not parsed or parsed.get("version") is None:
        return None
    return parsed["version"]


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


def render_file(context, stream="main", frame="####", config=None, dcc=None):
    """The frame-numbered output path for one stream of this context."""
    config = (config or Config()).for_project(context.project)
    settings = config.streams[stream]
    name = _fill(
        config.paths["render_file"], context, config,
        stem=render_stem(context, config),
        version=naming.format_version(context.version or 1, config),
        versioned=context.versioned(config=config),
        frame=frame,
        ext=settings.get("ext", "exr"),
    )
    return render_dir(context, stream, config, dcc) / name
