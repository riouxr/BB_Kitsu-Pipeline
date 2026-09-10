'''Making a picture out of a Maya scene.

A playblast of one frame, not a screenshot. A screenshot would capture
whatever floats over the viewport at the time - menus, the browser dialog,
the manipulator - and Kitsu would end up showing the pipeline's own UI as the
thumbnail.

``showOrnaments=False`` is what keeps the HUD, the axis and the view-cube out
of it. The Blender side learned the cost of leaving overlays in the hard way:
a thumbnail with a grid baked across it is not a picture of the model.

Nothing here raises. A missing thumbnail is not a reason to fail a publish
that otherwise has a comment worth posting - but the reason always comes back
with it, because a snapshot that quietly does nothing is indistinguishable
from a feature that is not there.
'''
import os
import tempfile

# Wide enough to read in the browser's version list and in Kitsu, small
# enough that nobody waits for it.
DEFAULT_WIDTH = 960


def _cmds():
    import maya.cmds as cmds
    return cmds


def viewport():
    '''The model panel a snapshot should be taken of, or ''.

    The focused one when it is a 3D view, because that is what the artist is
    actually looking at; otherwise the first model panel there is. Returns ''
    in batch, where there are none - which is not an error, just a session
    with no viewport to photograph.
    '''
    cmds = _cmds()

    try:
        panels = cmds.getPanel(type='modelPanel') or []
    except Exception:
        return ''
    if not panels:
        return ''

    try:
        focused = cmds.getPanel(withFocus=True)
    except Exception:
        focused = None
    if focused in panels:
        return focused
    return panels[0]


def _size():
    '''The thumbnail size, in the render's aspect ratio.'''
    cmds = _cmds()
    try:
        width = int(cmds.getAttr('defaultResolution.width'))
        height = int(cmds.getAttr('defaultResolution.height'))
    except Exception:
        width, height = 1920, 1080
    if width <= 0 or height <= 0:
        width, height = 1920, 1080

    if width <= DEFAULT_WIDTH:
        return width, height
    scale = float(DEFAULT_WIDTH) / float(width)
    return DEFAULT_WIDTH, max(1, int(round(height * scale)))


def snapshot(frame=None):
    '''Playblast one frame to a temporary PNG.

    Returns ``(path, problem)`` - the same shape the Nuke side uses, so the
    browser does not have to care which host it is running in.
    '''
    cmds = _cmds()

    panel = viewport()
    if not panel:
        return None, 'no viewport to snapshot - Maya is running without one'

    if frame is None:
        try:
            frame = cmds.currentTime(query=True)
        except Exception:
            frame = 1

    handle, path = tempfile.mkstemp(prefix='bb_snapshot_', suffix='.png')
    os.close(handle)
    os.remove(path)          # Maya writes it; it must not exist first.

    width, height = _size()

    try:
        cmds.playblast(
            completeFilename=path,
            frame=[frame],
            format='image',
            compression='png',
            widthHeight=(width, height),
            percent=100,
            quality=100,
            # Off-screen so a window passing over the viewport cannot end up
            # in the picture, and so it works with the panel not on top.
            offScreen=True,
            showOrnaments=False,
            viewer=False,
            clearCache=True,
            forceOverwrite=True,
        )
    except Exception as error:
        _discard(path)
        return None, 'could not playblast a snapshot: %s' % error

    if not os.path.isfile(path) or not os.path.getsize(path):
        _discard(path)
        return None, 'the playblast produced no file'

    return path, ''


def _discard(path):
    try:
        os.remove(path)
    except OSError:
        pass


discard = _discard


def describe():
    '''What a snapshot would be taken of, for the publish dialog to show.'''
    panel = viewport()
    if not panel:
        return ''

    cmds = _cmds()
    try:
        camera = cmds.modelPanel(panel, query=True, camera=True)
    except Exception:
        return panel
    return str(camera or panel)
