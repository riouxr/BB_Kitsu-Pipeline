'''Locating the shared core from inside Blender.

BB_core is not a Blender module - it is the DCC-agnostic package every
integration shares - so it has to be found in one of two places:

  * a development checkout, where the add-on sits at <repo>/blender/BB_pipeline
    and the core is at <repo>/BB_core. Nothing needs installing: edit the
    core, reload the add-on, done.
  * an installed extension, where the build script has copied BB_core in
    beside this file.

Both cases end up with the core's parent directory on sys.path, so the rest of
the add-on can just `from BB_core import ...`. If neither exists the add-on
still registers - its panel reports the problem instead of Blender silently
dropping the whole extension over an ImportError.
'''
import os
import sys

# realpath, not abspath: a development install is a directory junction from
# Blender's extensions folder back to the repository, and only resolving it
# puts BB_core's real location within reach of the checkout lookup below.
HERE = os.path.dirname(os.path.realpath(__file__))

# Set once the core has been located, for the panel to report against.
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
        error = ('BB_core not found next to the add-on - reinstall the '
                 'extension, or run it from a repository checkout')
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
