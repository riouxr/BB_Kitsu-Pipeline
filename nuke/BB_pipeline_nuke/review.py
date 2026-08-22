'''Turning a Nuke render into something Kitsu can play.

Comp renders EXR, and Kitsu cannot show an EXR. So publishing a Write builds a
movie from the frames it produced and uploads that, leaving the renders alone
- the same shape as the Blender side, through the same shared publish call.

**H.264 in an .mp4**, and the container is the deliberate part. Uploads go up
with ``normalize=false`` so Zou keeps exactly the bytes it is given, which
means the file has to be one a browser will play by itself. H.264 in .mp4 is
that everywhere; the same stream in a .mov is not - Safari plays it and Chrome
frequently will not. "QuickTime" is what everybody calls the review movie, but
the container that actually survives the trip is mp4.

The movie is built in Nuke rather than by shelling out to ffmpeg, so the
rendered frames go through Nuke's own colour management on the way.
'''
import glob
import os
import re
import tempfile

from BB_core import settings

from . import publish, session, stamp

state = session.state

# The frame placeholders Nuke writes into a path.
PRINTF = re.compile(r'%0?\d*d')
HASHES = re.compile(r'#+')

REVIEW_COLORSPACE = 'sRGB'


def _nuke():
    import nuke
    return nuke


def as_glob(path):
    '''A file pattern turned into something glob can match.'''
    pattern = PRINTF.sub('*', path)
    pattern = HASHES.sub('*', pattern)
    return pattern


def rendered_frames(path):
    '''Every frame on disk for a Write path, in order.'''
    if not path:
        return []

    pattern = as_glob(path)
    if pattern == path:
        # No frame placeholder at all - a single file, like a movie Write.
        return [path] if os.path.isfile(path) else []

    return sorted(glob.glob(pattern))


def frame_span(path, found=None):
    '''``(first, last)`` for what was rendered, read off the filenames.'''
    found = found if found is not None else rendered_frames(path)
    numbers = []
    for name in found:
        digits = re.findall(r'(\d+)', os.path.basename(name))
        if digits:
            numbers.append(int(digits[-1]))
    if not numbers:
        return 1, max(1, len(found))
    return min(numbers), max(numbers)


def build_movie(path, first=None, last=None):
    '''Render the frames at ``path`` to an H.264 mp4. Returns (path, problem).'''
    nuke = _nuke()

    found = rendered_frames(path)
    if not found:
        return None, 'nothing rendered at %s' % path

    if first is None or last is None:
        first, last = frame_span(path, found)

    handle, output = tempfile.mkstemp(prefix='bb_review_', suffix='.mp4')
    os.close(handle)
    os.remove(output)          # Nuke writes it; it must not exist first.
    output = output.replace('\\', '/')

    read = write = None
    try:
        read = nuke.nodes.Read(file=path, first=first, last=last,
                               origfirst=first, origlast=last)
        write = nuke.nodes.Write(inputs=[read], file=output, file_type='mov',
                                 colorspace=REVIEW_COLORSPACE,
                                 create_directories=True)
        # Codec knobs differ between Nuke versions and are not worth failing
        # over: without them the writer still produces a playable movie at its
        # default, which for mov64 is H.264.
        for knob, value in (('mov64_codec', 'h264'),
                            ('mov64_quality', 3),
                            ('mov64_fps', float(nuke.root().knob('fps').value()))):
            try:
                write.knob(knob).setValue(value)
            except Exception:
                pass

        nuke.execute(write, int(first), int(last))
    except Exception as error:
        _discard(output)
        return None, 'could not build the review movie: %s' % error
    finally:
        for node in (write, read):
            if node is not None:
                try:
                    nuke.delete(node)
                except Exception:
                    pass

    if not os.path.isfile(output) or not os.path.getsize(output):
        _discard(output)
        return None, 'the review movie came out empty'

    return output, ''


def submit(node, comment='', task_status_id=None):
    '''Publish what a Write rendered. Returns a note for the caller.'''
    entity_context, _source = stamp.read_current()

    blocked = publish.why_not(entity_context)
    if blocked:
        return blocked

    path = node.knob('file').evaluate() or node.knob('file').value()
    if not path:
        return 'this Write has no output path yet'

    movie, problem = build_movie(path)
    if problem:
        return problem

    try:
        return publish.send(entity_context, path, comment=comment,
                            task_status_id=task_status_id, preview=movie)
    finally:
        _discard(movie)


def _discard(path):
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


discard = _discard
