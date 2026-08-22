'''Making a picture out of a Nuke script.

Blender can grab its viewport; Nuke cannot. The only way to get an image out
of a comp is to render one, so a snapshot is a real render of a single frame
through a temporary Write node - which is also why it looks exactly like the
comp rather than like a screenshot of the interface.

The node is built with ``nuke.nodes.Write`` rather than ``createNode`` because
createNode connects itself to the current selection and leaves the new node
selected. Reaching into somebody's node graph to take a thumbnail should
change nothing about it, and this way the only trace is the frame that comes
out.
'''
import os
import tempfile

# The colourspace a review image should be written in. A comp works in linear
# and a PNG handed to Kitsu untagged would arrive looking flat and dark.
REVIEW_COLORSPACE = 'sRGB'


def _nuke():
    import nuke
    return nuke


def source_node():
    '''The node a snapshot should be taken of.

    The selected node if there is exactly one, because that is the explicit
    answer. Otherwise whatever the Viewer is looking at, because that is what
    the artist is actually judging.
    '''
    nuke = _nuke()

    try:
        selected = nuke.selectedNodes()
    except Exception:
        selected = []
    if len(selected) == 1:
        return selected[0]

    try:
        viewer = nuke.activeViewer()
    except Exception:
        viewer = None
    if viewer is None:
        return None

    try:
        node = viewer.node()
        return node.input(viewer.activeInput() or 0)
    except Exception:
        return None


def snapshot(frame=None):
    '''Render one frame to a temporary PNG. Returns the path, or None.

    Returns None rather than raising: a missing thumbnail is not a reason to
    fail a publish that otherwise has a comment worth posting.
    '''
    nuke = _nuke()

    target = source_node()
    if target is None:
        return None

    if frame is None:
        try:
            frame = int(nuke.frame())
        except Exception:
            frame = 1

    handle, path = tempfile.mkstemp(prefix='bb_snapshot_', suffix='.png')
    os.close(handle)
    os.remove(path)          # Nuke writes it; it must not exist first.

    write = None
    try:
        write = nuke.nodes.Write(
            inputs=[target],
            file=path.replace('\\', '/'),
            file_type='png',
            colorspace=REVIEW_COLORSPACE,
            create_directories=True,
        )
        nuke.execute(write, int(frame), int(frame))
    except Exception as error:
        nuke.tprint('BB Kitsu Pipeline: could not snapshot (%s)' % error)
        _discard(path)
        return None
    finally:
        if write is not None:
            try:
                nuke.delete(write)
            except Exception:
                pass

    if not os.path.isfile(path) or not os.path.getsize(path):
        _discard(path)
        return None

    return path


def _discard(path):
    try:
        os.remove(path)
    except OSError:
        pass


discard = _discard


def describe():
    '''What a snapshot would be taken of, for the publish dialog to show.'''
    node = source_node()
    if node is None:
        return ''
    try:
        return node.name()
    except Exception:
        return ''
