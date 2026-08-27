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

# The same folder as Blender addresses it, before the link is resolved. A
# junction install has two names for one directory, and a module recorded
# under either has to be recognised.
HERE_AS_INSTALLED = os.path.dirname(os.path.abspath(__file__))

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


def _forget_older_build():
    """Erase what a previous version of this add-on left in the interpreter.

    Until 0.3.1 the core went on ``sys.path`` and was imported as top-level
    ``BB_core``. Replacing the files does not undo either: modules and path
    entries live in the interpreter, not on disk, so an upgrade in place
    leaves the old build's footprints behind and Blender reports them
    against the new one - the policy warnings come back on an add-on that no
    longer does anything wrong, and only a restart clears them.

    Only entries pointing inside this add-on's own folder are touched, so a
    development checkout - where ``BB_core`` is legitimately top-level and
    lives one level up, shared with the Nuke package and the tests - is left
    exactly as it is.
    """
    roots = tuple(folder + os.sep
                  for folder in {HERE, HERE_AS_INSTALLED})

    for name in [name for name in sys.modules
                 if name == 'BB_core' or name.startswith('BB_core.')]:
        module = sys.modules.get(name)
        origin = getattr(module, '__file__', None) or ''
        if not origin:
            continue
        origin = os.path.abspath(origin)
        try:
            resolved = os.path.realpath(origin)
        except OSError:
            resolved = origin
        if origin.startswith(roots) or resolved.startswith(roots):
            del sys.modules[name]

    for entry in [entry for entry in sys.path if entry]:
        here = os.path.abspath(entry)
        try:
            resolved = os.path.realpath(entry)
        except OSError:
            resolved = here
        if (here in (HERE, HERE_AS_INSTALLED)
                or resolved in (HERE, HERE_AS_INSTALLED)
                or here.startswith(roots) or resolved.startswith(roots)):
            sys.path.remove(entry)


def bootstrap():
    '''Make ``from .BB_core import ...`` work. True when it does.'''
    global error, available

    _forget_older_build()

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
