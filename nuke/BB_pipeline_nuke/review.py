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


def has_frame_pattern(path):
    """True when a path carries a frame placeholder rather than one frame."""
    return bool(path) and bool(PRINTF.search(path) or HASHES.search(path))


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


def _belongs_elsewhere(entity_context, path):
    """Why this Write's output is not this script's, or '' when it is.

    A Write copied in from another shot's script keeps that shot's path, and
    publishing it would post one shot's frames against another - the same
    mistake the Blender side made by holding on to the last render across a
    file load, and just as invisible until review.

    Compared against the render folder for the open script rather than
    against the name, so a Write pointed anywhere unexpected is caught, not
    only one that happens to be named differently.
    """
    from BB_core import workfiles

    try:
        config = session.config_for(entity_context)
        # The task's render folder, not the version's. Which shot and which
        # task is the dangerous mismatch; the version is not checked here
        # because versioning up repoints the Writes itself, and a Write
        # still on the previous version is a repoint away rather than a
        # publish against the wrong entity.
        mine = str(workfiles.render_dir(entity_context, 'main', config).parent)
    except Exception:
        # No render root configured, or no context to build one from. The
        # publish will report that in its own words.
        return ''

    here = os.path.normpath(str(path)).lower()
    if here.startswith(os.path.normpath(mine).lower()):
        return ''

    return ('that Write renders to %s, which is not the render folder '
            'for this task - point it at this one with Set Output Path'
            % os.path.dirname(str(path)))


def build_still(path, frame=None):
    '''Convert one rendered frame to a PNG for Kitsu. Returns (path, problem).

    A movie is for a sequence. Comp renders EXR, which Kitsu cannot show, so
    a single frame still has to be converted - but into an image, not a
    one-frame MP4 that Kitsu plays as a flicker and shows at video quality.
    '''
    nuke = _nuke()

    found = rendered_frames(path)
    if not found:
        return None, 'nothing rendered at %s' % path

    if frame is None:
        frame, _last = frame_span(path, found)

    handle, output = tempfile.mkstemp(prefix='bb_review_', suffix='.png')
    os.close(handle)
    os.remove(output)          # Nuke writes it; it must not exist first.
    output = output.replace('\\', '/')

    read = write = None
    try:
        read = nuke.nodes.Read(file=path, first=frame, last=frame,
                               origfirst=frame, origlast=frame)
        write = nuke.nodes.Write(inputs=[read], file=output, file_type='png',
                                 colorspace=REVIEW_COLORSPACE,
                                 create_directories=True)
        nuke.execute(write, int(frame), int(frame))
    except Exception as error:
        _discard(output)
        return None, 'could not build the review image: %s' % error
    finally:
        for node in (write, read):
            if node is not None:
                try:
                    nuke.delete(node)
                except Exception:
                    pass

    if not os.path.isfile(output) or not os.path.getsize(output):
        _discard(output)
        return None, 'the review image came out empty'

    return output, ''


def prepare(path):
    '''The file to send to Kitsu, and whether it is a sequence.

    Returns ``(path, is_sequence, problem)``. One frame goes as an image and
    a run of them as a movie, the same rule the Blender side follows - the
    difference being that Nuke has to convert either way, because comp
    renders EXR and Kitsu shows neither.
    '''
    found = rendered_frames(path)
    if not found:
        return None, False, 'nothing rendered at %s' % path

    if len(found) == 1:
        first, _last = frame_span(path, found)
        made, problem = build_still(path, first)
        return made, False, problem

    made, problem = build_movie(path)
    return made, True, problem


def submit(node, comment='', task_status_id=None):
    '''Publish what a Write rendered. Returns a note for the caller.'''
    entity_context, _source = stamp.read_current()

    blocked = publish.why_not(entity_context)
    if blocked:
        return blocked

    from . import writenode

    path = writenode.pattern_of(node)
    if not path:
        return 'this Write has no output path yet'

    stale = _belongs_elsewhere(entity_context, path)
    if stale:
        return stale

    preview, sequence, problem = prepare(path)
    if problem:
        return problem

    try:
        note = publish.send(entity_context, path, comment=comment,
                            task_status_id=task_status_id, preview=preview)
    finally:
        _discard(preview)

    return note + _version_up_after_publish(sequence)


def _version_up_after_publish(sequence=True):
    '''Cut the next script version once a render has been published.

    So the script and the Kitsu revision stay in step: publishing three
    renders from one saved version puts three revisions against it and
    nothing on disk tells them apart. Publishing closes a version instead,
    the same as the Blender side.
    '''
    nuke = _nuke()

    when = settings.get('version_up_on_publish', 'SEQUENCE')
    # Read leniently: this was a plain true/false before it was a choice.
    if when is True:
        when = 'ALWAYS'
    elif when is False:
        when = 'NEVER'

    if when == 'NEVER':
        return ''
    if when == 'SEQUENCE' and not sequence:
        # A single image is a look, not a delivery.
        return ''

    from . import scripts

    try:
        made = scripts.save_next_version()
    except Exception as error:
        nuke.tprint('BB Kitsu Pipeline: could not version up after publish (%s)'
                    % error)
        return ''

    import os as _os
    return ' - now on %s' % _os.path.basename(made) if made else ''


def _discard(path):
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


discard = _discard
