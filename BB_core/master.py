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

from . import versioning, workfiles
from .config import Config

MASTER_NAME = "Master"


def master_dir(context, stream="main", config=None):
    """Where the copy for one stream lives."""
    version_dir = workfiles.render_dir(context, stream, config)
    return version_dir.parent / MASTER_NAME


def set_master(context, stream="main", config=None):
    """Copy this stream's version into Master, replacing whatever was there.

    Returns ``(folder, skipped_names)``.

    Every frame is renamed to ``master.<frame>.<ext>`` on the way in - not
    kept as ``<stem>_v003.<frame>.<ext>``. A version-numbered name sitting
    in Master is a trap waiting for the next flag: if one frame is locked
    (see below) and cannot be replaced, an old file named ``..._v001...``
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
    target = workfiles.render_dir(context, stream, config)
    if not target.is_dir():
        raise ValueError("no render at %s" % target)

    link = master_dir(context, stream, config)
    link.mkdir(parents=True, exist_ok=True)

    wanted_names = set()
    skipped = []
    for entry in target.iterdir():
        if not entry.is_file():
            continue
        match = workfiles._FRAME.search(entry.name)
        if not match:
            continue
        dest_name = "master.%s%s" % (match.group(1), entry.suffix)
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


def master_frames(context, stream="main", config=None):
    """``(pattern, first, last)`` for what is in Master right now, or None."""
    config = (config or Config()).for_project(context.project)
    if stream not in config.streams:
        return None

    folder = master_dir(context, stream, config)
    if not folder.is_dir():
        return None

    ext = config.streams[stream].get("ext", "exr")
    frames = sorted(folder.glob("master.*.%s" % ext))
    if not frames:
        return None

    pattern, first, last = workfiles._frame_pattern(frames)
    return (pattern, first, last) if pattern else None


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
