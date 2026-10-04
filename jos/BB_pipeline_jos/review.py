'''Turning a JOS render into something Kitsu can play.

Comp renders EXR, and Kitsu cannot show an EXR. So publishing a Write builds a
movie from the frames it produced and uploads that, leaving the renders alone
- the same shape as the Blender and Nuke sides, through the same shared
publish call.

**H.264 in an .mp4**, as on the Nuke side: uploads go up with
``normalize=false`` so Zou keeps exactly the bytes it is given, and H.264 in
.mp4 is what every browser plays by itself.

The movie is built in JOS rather than by shelling out to ffmpeg, so the
rendered frames go through JOS's own colour management (the project's OCIO
display transform) on the way.
'''
import glob
import os
import re
import tempfile

from BB_core import settings

from . import publish, session, stamp

state = session.state

# The frame placeholders a Write path carries (Nuke's and JOS's).
PRINTF = re.compile(r'%0?\d*d')
HASHES = re.compile(r'#+')


def _jos():
    import jos
    return jos


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


def _temporary(suffix):
    '''A path for JOS to write (which must not exist yet).'''
    handle, output = tempfile.mkstemp(prefix='bb_review_', suffix=suffix)
    os.close(handle)
    os.remove(output)
    return output.replace(os.sep, '/')


def _render_temporary(path, first, last, output, file_type, extra):
    """Read ``path`` and write first..last to ``output``, then tidy up.

    Both nodes are temporary: deleted afterwards, with the project's modified
    flag put back, so publishing leaves the comp as it was.
    """
    jos = _jos()

    was_modified = jos.modified()
    read = write = None
    try:
        with jos.undo_group('Kitsu review'):
            read = jos.create_node('Read', name='KitsuReviewRead1', file=path,
                                   first=int(first), last=int(last))
            write = jos.create_node('Write', name='KitsuReview1', file=output,
                                    file_type=file_type,
                                    transformType='display',
                                    create_directories=True, **extra)
            write.set_input(0, read)
        jos.render(write.name, int(first), int(last))
    finally:
        for node in (write, read):
            if node is not None:
                try:
                    jos.delete(node)
                except Exception:
                    pass
        if not was_modified:
            jos.set_modified(False)


def _build(path, first, last, suffix, file_type, extra, what):
    found = rendered_frames(path)
    if not found:
        return None, 'nothing rendered at %s' % path
    if first is None or last is None:
        first, last = frame_span(path, found)

    output = _temporary(suffix)
    try:
        _render_temporary(path, first, last, output, file_type, extra)
    except Exception as error:
        _discard(output)
        return None, 'could not build the review %s: %s' % (what, error)

    if not os.path.isfile(output) or not os.path.getsize(output):
        _discard(output)
        return None, 'the review %s came out empty' % what
    return output, ''


def build_movie(path, first=None, last=None):
    '''Render the frames at ``path`` to an H.264 mp4. Returns (path, problem).'''
    return _build(path, first, last, '.mp4', 'mov', {'mov_codec': 'h264'},
                  'movie')


def build_still(path, frame=None):
    '''Convert one rendered frame to a PNG for Kitsu. Returns (path, problem).

    A movie is for a sequence; one frame goes as an image, not a one-frame
    MP4 that Kitsu plays as a flicker and shows at video quality.
    '''
    if frame is None and rendered_frames(path):
        frame, _last = frame_span(path)
    return _build(path, frame, frame, '.png', 'png', {}, 'image')


def _belongs_elsewhere(entity_context, path):
    """Why this Write's output is not this project's, or '' when it is.

    A Write copied in from another shot's project keeps that shot's path, and
    publishing it would post one shot's frames against another. Compared
    against the task's render folder (not the version's: versioning up
    repoints the Writes itself).
    """
    from BB_core import workfiles

    try:
        config = session.config_for(entity_context)
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


def prepare(path):
    '''The file to send to Kitsu, and whether it is a sequence.

    Returns ``(path, is_sequence, problem)``. One frame goes as an image and
    a run of them as a movie, the same rule the Blender side follows.
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


def submit(node, comment='', task_status_id=None, master=False):
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

    flagged = ''
    if master:
        # The frames the Write rendered, not the movie made from them -
        # Master is what the next comp reads, and a comp reads frames.
        from BB_core import master as master_mod
        try:
            master_mod.set_master(entity_context, writenode.stream_of(node),
                                  session.config_for(entity_context),
                                  session.DCC)
            flagged = ' and made master'
        except Exception as error:
            flagged = ' - but it could not be made master (%s)' % error

    try:
        note = publish.send(entity_context, path, comment=comment,
                            task_status_id=task_status_id, preview=preview,
                            master=master)
    finally:
        _discard(preview)
    note += flagged

    return note + _version_up_after_publish(sequence)


def _version_up_after_publish(sequence=True):
    '''Cut the next project version once a render has been published.

    So the project and the Kitsu revision stay in step, as on the Blender and
    Nuke sides.
    '''
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
        print('BB Kitsu Pipeline: could not version up after publish (%s)'
              % error)
        return ''

    return ' - now on %s' % os.path.basename(made) if made else ''


def _discard(path):
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


discard = _discard
