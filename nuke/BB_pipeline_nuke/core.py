'''Locating the shared core from inside Nuke.

Same problem the Blender add-on has and the same two answers: a development
checkout, where this package sits at <repo>/nuke/BB_pipeline_nuke and the
core is at <repo>/BB_core, or an installed copy with BB_core beside it.

Nothing here imports nuke, so it can be exercised by a plain interpreter.
'''
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))

error = ''
available = False


def _candidates():
    # Development checkout first: when both exist, the working copy is the one
    # being edited and is what the developer expects to be running.
    yield os.path.abspath(os.path.join(HERE, '..', '..'))
    yield HERE


def _locate():
    for root in _candidates():
        if os.path.isdir(os.path.join(root, 'BB_core')):
            return root
    return None


def bootstrap():
    '''Put BB_core on sys.path. True when it is importable afterwards.'''
    global error, available

    root = _locate()
    if root is None:
        error = ('BB_core not found next to the Nuke package - reinstall, '
                 'or run it from a repository checkout')
        available = False
        return False

    if root not in sys.path:
        sys.path.insert(0, root)

    try:
        import BB_core  # noqa: F401
    except Exception as exception:
        error = 'BB_core failed to import: %s' % exception
        available = False
        return False

    error = ''
    available = True
    return True
