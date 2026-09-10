'''Finding what a Maya scene rendered, so it can be reviewed.

Deliberately narrower than the Nuke side. Nuke builds an H.264 movie out of a
comp's frames because a comp *is* a sequence and Kitsu cannot show an EXR;
Maya's own review picture is a playblast of the viewport, which
:mod:`capture` already produces, and a still is what most Maya publishes want
- a model, a rig, a lookdev turntable frame.

So this module scans, and does not encode. It reports what is on disk for a
version and picks a representative frame out of it; the frame goes to Kitsu
through the same shared publish call every other host uses. A studio that
wants a movie out of a render here should point the Nuke side at it, which
already does exactly that and does it through colour management.
'''
import glob
import os
import re

from BB_core import workfiles

from . import session

state = session.state

# The frame placeholders a render path can carry.
PRINTF = re.compile(r'%0?\d*d')
HASHES = re.compile(r'#+')

# What Kitsu can actually display without transcoding. An EXR uploaded as a
# preview arrives as a file nobody can open in the browser.
VIEWABLE = ('.png', '.jpg', '.jpeg', '.tif', '.tiff')


def has_frame_pattern(path):
    '''True when a path carries a frame placeholder rather than one frame.'''
    return bool(path) and bool(PRINTF.search(path) or HASHES.search(path))


def as_glob(path):
    '''A file pattern turned into something glob can match.'''
    pattern = PRINTF.sub('*', str(path))
    pattern = HASHES.sub('*', pattern)
    return pattern


def rendered_frames(path):
    '''Every frame on disk for a render path, in order.'''
    if not path:
        return []

    pattern = as_glob(path)
    if pattern == str(path):
        # No frame placeholder at all - a single file.
        return [str(path)] if os.path.isfile(str(path)) else []

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


def versions(entity_context, stream='main'):
    '''The rendered versions on disk for a context, newest first.'''
    if entity_context is None:
        return []
    try:
        config = session.config_for(entity_context)
        return workfiles.render_versions(entity_context, stream, config)
    except Exception as error:
        state.say(str(error), error=True)
        return []


def pick_frame(found, wanted=None):
    """The frame worth showing out of a rendered sequence.

    The middle one by default, not the first. A sequence's first frame is
    routinely the least representative thing in it - a fade from black, a
    character not yet on screen - and a review thumbnail that shows nothing
    is a review thumbnail nobody trusts.

    Only formats Kitsu can display are considered. An EXR is the normal
    output of a Maya render and the one thing that must not be uploaded as a
    preview, because it arrives as a file the browser cannot open.
    """
    viewable = [name for name in (found or [])
                if os.path.splitext(name)[1].lower() in VIEWABLE]
    if not viewable:
        return '', ('nothing viewable in that render - Kitsu shows %s, and '
                    'these are %s'
                    % ('/'.join(e.lstrip('.') for e in VIEWABLE[:3]),
                       _formats(found) or 'not there'))

    if wanted is not None:
        for name in viewable:
            digits = re.findall(r'(\d+)', os.path.basename(name))
            if digits and int(digits[-1]) == int(wanted):
                return name, ''

    return viewable[len(viewable) // 2], ''


def _formats(found):
    '''The distinct extensions in a list of files, for an error message.'''
    seen = []
    for name in found or []:
        extension = os.path.splitext(name)[1].lower().lstrip('.')
        if extension and extension not in seen:
            seen.append(extension)
    return '/'.join(seen)
