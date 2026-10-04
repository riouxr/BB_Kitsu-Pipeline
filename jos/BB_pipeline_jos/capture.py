'''Making a picture out of a JOS project.

As on the Nuke side, a snapshot is a real render of a single frame through a
temporary Write node - so it looks exactly like the comp, through the
project's OCIO display transform, rather than like a screenshot of the
interface. The Write is deleted afterwards and the project's modified flag
put back, so the only trace is the frame that comes out.
'''
import os
import tempfile


def _jos():
    import jos
    return jos


def source_node():
    '''The node a snapshot should be taken of.

    The selected node if there is exactly one, because that is the explicit
    answer. Otherwise whatever the Viewer is looking at.
    '''
    jos = _jos()

    try:
        selected = jos.selected_nodes()
    except Exception:
        selected = []
    if len(selected) == 1:
        return selected[0]
    try:
        return jos.viewed_node()
    except Exception:
        return None


def render_png(target, path, frame):
    '''Render one frame of ``target`` to ``path`` as a display-referred PNG.

    Raises on failure. Shared with review.py's stills.
    '''
    jos = _jos()

    was_modified = jos.modified()
    write = None
    try:
        with jos.undo_group('Kitsu snapshot'):
            write = jos.create_node('Write', name='KitsuSnapshot1',
                                    file=path.replace('\\', '/'),
                                    file_type='png',
                                    transformType='display',
                                    create_directories=True)
            write.set_input(0, target)
        jos.render(write.name, int(frame), int(frame))
    finally:
        if write is not None:
            try:
                jos.delete(write)
            except Exception:
                pass
        if not was_modified:
            jos.set_modified(False)


def snapshot(frame=None):
    '''Render one frame to a temporary PNG.

    Returns ``(path, problem)``. Never raises: a missing thumbnail is not a
    reason to fail a publish - but the reason comes back with it.
    '''
    jos = _jos()

    target = source_node()
    if target is None:
        return None, 'nothing to snapshot - select a node or view one'

    if frame is None:
        try:
            frame = int(jos.frame())
        except Exception:
            frame = 1

    handle, path = tempfile.mkstemp(prefix='bb_snapshot_', suffix='.png')
    os.close(handle)
    os.remove(path)          # JOS writes it.

    try:
        render_png(target, path, frame)
    except Exception as error:
        print('BB Kitsu Pipeline: could not snapshot (%s)' % error)
        _discard(path)
        return None, 'could not render a snapshot: %s' % error

    if not os.path.isfile(path) or not os.path.getsize(path):
        _discard(path)
        return None, 'the snapshot render produced no file'

    return path, ''


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
        return node.name
    except Exception:
        return ''
