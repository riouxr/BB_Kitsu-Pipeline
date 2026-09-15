"""The "Master" folder: a real copy of whichever version was flagged master.

A directory link (an NTFS junction, or a symlink) looked like the cleaner
mechanism at first: point a stable ``Master`` folder at whichever version
counts, and nothing downstream ever needs to change. Neither half of that
held up on a real setup, though - a mapped network drive refuses junctions
outright ("local NTFS volumes are required"), and a symlink needs elevation
nothing here should be granted just to flag a render. So this copies the
frames instead: a real folder, on any drive, that Nuke or Resolve reads
exactly like any other render output, no special privilege or filesystem
feature required. The cost is disk space and copy time proportional to the
sequence, paid once per flag rather than never.

A ``[[master:vN]]`` comment (see ``versioning.format_master_tag`` and
``bb_launch_server.py``'s ``/set_master`` route) still goes up alongside the
copy - not the mechanism, just a durable record of which version and when,
for the "Master (v005)" label in Nuke/Resolve's browsers and for anyone
reading a task's history later.
"""
import shutil
from pathlib import Path

from . import naming, versioning, workfiles
from .config import Config

MASTER_NAME = "Master"
MASTER_WORD = "master"


def master_dir(context, stream="main", config=None, dcc=None):
    """Where the copy for one stream lives.

    Beside the version folders it was copied from, so inside the rendering
    application's own render folder. With no application named - a reader
    that does not know which one rendered - the first of the candidates that
    exists, and the pre-folder location when none does.
    """
    if dcc:
        return workfiles.render_dir(context, stream, config, dcc).parent / MASTER_NAME
    candidates = master_dirs(context, stream, config)
    for folder in candidates:
        if folder.is_dir():
            return folder
    return candidates[-1]


def master_dirs(context, stream="main", config=None):
    """Every place a master for this stream could be, newest layout first."""
    config = (config or Config()).for_project(context.project)
    found = []
    for renders in workfiles.render_folders(config):
        folder = workfiles._render_dir(context, stream, config, renders).parent / MASTER_NAME
        if folder not in found:
            found.append(folder)
    return found


def _rendered_dir(context, stream, config):
    """The version folder that actually holds this version's frames."""
    return workfiles.find_render_dir(context, stream, config)


def _master_stem(context, config):
    """The version's own stem with its version swapped for the word "master".

    ``sh01_v003`` becomes ``sh01_master`` - the entity and task still read
    off the filename the way any other render's does, only the version
    number itself is gone, replaced by the one word that is true regardless
    of which version it was.
    """
    stem = workfiles.render_stem(context, config)
    version_token = "v" + naming.format_version(context.version or 1, config)
    return stem.replace(version_token, "master", 1)


def set_master(context, stream="main", config=None, dcc=None):
    """Copy this stream's version into Master, replacing whatever was there.

    Returns ``(folder, skipped_names)``.

    Every frame is renamed from ``<stem>_v003.<frame>.<ext>`` to
    ``<stem>_master.<frame>.<ext>`` on the way in - the version swapped out,
    everything else about the name kept. A version-numbered name sitting in
    Master is a trap waiting for the next flag: if one frame is locked (see
    below) and cannot be replaced, an old file still saying ``..._v001...``
    would sit right next to the new ``..._v003...`` ones, and frame-pattern
    detection - which works by filename, not by which version a file
    happens to hold - would see two different name patterns claiming the
    same folder rather than one. A name that never encodes a version has
    nothing to disagree about: the frame number is the only thing that
    still varies, so the same name is always exactly the thing that should
    be there once every frame has actually landed.

    File by file, in place - not a wholesale ``rmtree`` then ``copytree``.
    Nuke keeps a Read node's frames open for as long as the script pointed
    at Master stays loaded, and Windows refuses to delete a file another
    process still has open; clearing the folder first turned "flag a new
    master" into a silent no-op the moment anyone had Master open anywhere.
    Overwriting a file in place is something Windows generally allows even
    while another process holds it open for reading, and a frame that
    genuinely can't be touched is skipped rather than failing the whole
    flag - one stale frame until whatever is locking it lets go beats
    nothing updating at all.
    """
    target = (workfiles.render_dir(context, stream, config, dcc) if dcc
              else _rendered_dir(context, stream, config))
    if not target.is_dir():
        raise ValueError("no render at %s" % target)

    # Beside the version folder it came from, whichever render folder that is.
    link = target.parent / MASTER_NAME
    link.mkdir(parents=True, exist_ok=True)

    stem = _master_stem(context, config)
    wanted_names = set()
    skipped = []
    for entry in target.iterdir():
        if not entry.is_file():
            continue
        match = workfiles._FRAME.search(entry.name)
        if not match:
            continue
        dest_name = "%s.%s%s" % (stem, match.group(1), entry.suffix)
        wanted_names.add(dest_name)
        try:
            shutil.copy2(entry, link / dest_name)
        except OSError:
            skipped.append(dest_name)

    for entry in list(link.iterdir()):
        if entry.is_file() and entry.name not in wanted_names:
            try:
                entry.unlink()
            except OSError:
                pass

    return link, skipped


def master_frames(context, stream="main", config=None, dcc=None):
    """``(pattern, first, last)`` for what is in Master right now, or None.

    ``_master_stem`` comes out the same regardless of which version number
    ``context`` happens to carry - only the version token gets swapped out,
    and the entity/task portion of the name never depended on it - so this
    never needs to know which version is actually flagged to find Master's
    files; it just looks for the one name every ``set_master`` ever writes.
    """
    config = (config or Config()).for_project(context.project)
    if stream not in config.streams:
        return None

    ext = config.streams[stream].get("ext", "exr")
    stem = _master_stem(context.at_version(context.version or 1), config)

    folders = ([master_dir(context, stream, config, dcc)] if dcc
               else master_dirs(context, stream, config))
    for folder in folders:
        if not folder.is_dir():
            continue
        frames = sorted(folder.glob("%s.*.%s" % (stem, ext)))
        if not frames:
            continue
        pattern, first, last = workfiles._frame_pattern(frames)
        if pattern:
            return pattern, first, last
    return None


def every_master(context, config=None):
    """``[(stream, pattern, first, last)]`` for each stream with a master.

    A playblast flagged master is in the playblast stream's own Master
    folder, so looking only in ``main``'s misses it.
    """
    config = (config or Config()).for_project(context.project)
    found = []
    for stream in sorted(config.streams):
        frames = master_frames(context, stream, config)
        if frames is not None:
            found.append((stream,) + tuple(frames))
    return found


# -- scene files ----------------------------------------------------------------
#
# The same idea for the scene a version was saved as: one file whose name never
# changes, holding whichever version was last flagged. It is what another task
# links - a lighting scene linking the model's master picks up the next approved
# model by reopening, not by being re-pointed at v009.

def _masterise(name, context, config):
    """*name* with its version token swapped for the word master.

    The last occurrence, not the first: an entity called "v2-rig" must keep
    its own name.
    """
    token = "v" + naming.format_version(context.version or 1, config)
    head, found, tail = name.rpartition(token)
    return head + MASTER_WORD + tail if found else name


def master_scene_file(context, dcc, config=None):
    """Where a task's master scene file lives: beside its versions.

    ``knife_v008.blend`` is flagged as ``knife_master.blend`` in the same
    folder. The same folder on purpose - a copy anywhere else would carry
    relative paths to textures and linked libraries that no longer resolve
    from where it sits.
    """
    config = (config or Config()).for_project(context.project)
    context = context.at_version(context.version or 1)
    version_file = workfiles.work_file(context, dcc, context.version, config)
    return version_file.with_name(_masterise(version_file.name, context, config))


def master_thumb_file(context, dcc, config=None):
    """The picture the browser shows for the master scene."""
    config = (config or Config()).for_project(context.project)
    context = context.at_version(context.version or 1)
    folder = workfiles.scene_dir(context, dcc, config) / workfiles.THUMB_DIR
    return folder / ("%s.png" % _masterise(context.versioned(config=config),
                                           context, config))


def set_master_scene(context, dcc, config=None, source=None):
    """Copy a saved version over the task's master scene. Returns its path.

    ``source`` defaults to the version file ``context`` names. Copied in
    place rather than deleted and replaced, for the reason set_master gives:
    Windows lets a file somebody has open be overwritten, not removed, and a
    master is exactly the file other scenes have open.
    """
    config = (config or Config()).for_project(context.project)
    source = Path(source) if source else workfiles.work_file(
        context, dcc, context.version, config)
    if not source.is_file():
        raise ValueError("no scene file at %s" % source)

    target = master_scene_file(context, dcc, config)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(source), str(target))

    # The version's picture as well, so the master is recognisable in the
    # browser; a missing one is not worth failing the flag over.
    picture = workfiles.thumb_file(context, dcc, context.version, config)
    if picture.is_file():
        try:
            thumb = master_thumb_file(context, dcc, config)
            thumb.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(picture), str(thumb))
        except OSError:
            pass
    return target


def master_scene(context, dcc, config=None):
    """The master scene file if one exists, else None."""
    found = master_scene_file(context, dcc, config)
    return found if found.is_file() else None


def current_master(client, task_id):
    """The version most recently flagged master for this task, or None.

    Read back from the comment tag - not what actually decides what is in
    Master (the copy already happened by the time this is asked), just the
    label ("Master (v005)") shown next to it. Several ``[[master:vN]]``
    comments can exist over a task's life - every retarget posts a new one
    rather than editing the last - so this finds the *newest* one rather
    than trusting Kitsu's own comment order, which is not relied on here.
    """
    comments = client.comments(task_id) or []
    newest_version = None
    newest_time = None
    for comment in comments:
        version = versioning.parse_master_tag(comment.get("text"))
        if version is None:
            continue
        created = comment.get("created_at") or ""
        if newest_time is None or created > newest_time:
            newest_time = created
            newest_version = version
    return newest_version
