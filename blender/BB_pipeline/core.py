'''Binding the shared core into the add-on.

BB_core is not a Blender module - it is the DCC-agnostic package every
integration shares - so it lives in one of two places:

  * an installed extension, where the build script has copied BB_core in
    beside this file, and a plain ``from .BB_core import ...`` finds it;
  * a development checkout, where the add-on sits at <repo>/blender/BB_pipeline
    and the core is at <repo>/BB_core. Nothing needs installing: edit the
    core, reload the add-on, done.

The second case used to put the repository root on ``sys.path`` and import
``BB_core`` as a top-level module. Blender's extension policy forbids both,
and says so in the preferences - one warning for the ``sys.path`` write and
one for every top-level module the extension brings with it. Neither is
avoidable by moving files around here: this drive is exFAT, so the checkout
cannot hold a junction the way Blender's extensions folder does.

So the checkout is loaded *as* ``BB_pipeline.BB_core`` instead. Giving the
spec a ``submodule_search_locations`` pointing at the real directory is what
makes ``from .BB_core import settings`` resolve to the file being edited,
with nothing added to ``sys.path`` and no top-level module in sight.

If neither case works the add-on still registers - its panel reports the
problem rather than Blender silently dropping the whole extension over an
ImportError.
'''
import importlib.util
import os
import sys

# realpath, not abspath: a development install is a directory junction from
# Blender's extensions folder back to the repository, and only resolving it
# puts BB_core's real location within reach of the checkout lookup below.
HERE = os.path.dirname(os.path.realpath(__file__))

# Set once the core has been located, for the panel to report against.
error = ''
available = False


def _checkout_core():
    '''<repo>/BB_core for a development install, or None.'''
    root = os.path.abspath(os.path.join(HERE, '..', '..'))
    folder = os.path.join(root, 'BB_core')
    return folder if os.path.isdir(folder) else None


def _bind(folder):
    '''Load *folder* as this package's own BB_core submodule.'''
    name = '%s.BB_core' % __package__
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(folder, '__init__.py'),
        submodule_search_locations=[folder])
    if spec is None or spec.loader is None:
        raise ImportError('no package at %s' % folder)

    module = importlib.util.module_from_spec(spec)
    # Registered before exec_module, so the core's own relative imports find
    # the package they belong to while it is still being built.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def bootstrap():
    '''Make ``from .BB_core import ...`` work. True when it does.'''
    global error, available

    # Bundled beside this file: the shape every installed extension has.
    try:
        from . import BB_core  # noqa: F401
        error = ''
        available = True
        return True
    except ImportError:
        pass

    folder = _checkout_core()
    if folder is None:
        error = ('BB_core not found next to the add-on - reinstall the '
                 'extension, or run it from a repository checkout')
        available = False
        return False

    try:
        _bind(folder)
    except Exception as exception:
        error = 'BB_core failed to import: %s' % exception
        available = False
        return False

    error = ''
    available = True
    return True


def version():
    """The add-on's version, as the browser shows it.

    Read from the manifest rather than hard-coded, because the manifest is
    what Blender installs and reports - so what the browser prints and what
    the Extensions list says can never disagree. A junctioned add-on and a
    stale copy look identical from the menu otherwise.
    """
    import os
    import re

    manifest = os.path.join(os.path.dirname(__file__), 'blender_manifest.toml')
    try:
        with open(manifest, encoding='utf-8') as handle:
            found = re.search(r'^version\s*=\s*"([^"]+)"', handle.read(), re.M)
    except OSError:
        return '?'
    return found.group(1) if found else '?'
