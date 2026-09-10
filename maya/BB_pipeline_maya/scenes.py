'''Opening, creating and versioning Maya scenes.

The same rule the other two hosts follow: a version number is discovered
once, by the core, from what is on disk - never typed in, never inferred
twice - and the scene that gets saved carries the context that named it.
'''
import os
import sys

from BB_core import versioning, workfiles

from . import session, stamp

state = session.state

# Maya wants to be told which of its two formats it is writing, and the
# answer is not derivable from the command - only from the extension.
_TYPES = {'.ma': 'mayaAscii', '.mb': 'mayaBinary'}


class SceneError(Exception):
    '''Something the artist needs to fix before this can work.'''


def _cmds():
    import maya.cmds as cmds
    return cmds


def file_type(path):
    '''Maya's name for the format a path implies.'''
    return _TYPES.get(os.path.splitext(str(path))[1].lower(), 'mayaAscii')


def is_modified():
    '''True when the scene has unsaved changes.'''
    try:
        return bool(_cmds().file(query=True, modified=True))
    except Exception:
        return False


def forget_our_edit():
    '''Clear the modified flag after a change only the pipeline made.

    Stamping the scene, aiming the render output and applying the frame range
    all mark it modified without anybody having touched the work. Asking
    "save your changes?" about those is asking about work nobody did.
    '''
    try:
        _cmds().file(modified=False)
    except Exception:
        pass


def current_path():
    '''The open scene's path, or '' for one that has never been saved.'''
    return stamp.current_path()


def open_version(path, known=None):
    '''Open a scene in this session.

    Asking about unsaved work is the caller's job - by the time this is
    called, whether to save has been asked and answered, so ``force`` is
    correct here rather than reckless.

    ``known`` is the context the browser opened it from. A scene written
    before it was stamped, or by hand, carries nothing to identify it, and
    throwing away what the browser already knew is how a file opened from the
    browser ends up unable to say which project it is in.
    '''
    cmds = _cmds()

    if not os.path.isfile(path):
        raise SceneError('File is gone: %s' % path)

    try:
        cmds.file(str(path), open=True, force=True)
    except Exception as error:
        raise SceneError('Could not open %s: %s' % (path, error))

    state.context = stamp.read() or known
    # A freshly opened scene has no unsaved changes, whatever reading it may
    # have touched.
    forget_our_edit()
    return str(path)


def open_elsewhere(path):
    """Launch another Maya on *path*. Returns the path, or raises.

    A second session rather than replacing this one, for the times a scene is
    still wanted on screen while another is opened. Detached on purpose: the
    new Maya outlives whatever started it.
    """
    import subprocess

    executable = maya_executable()
    if not executable:
        raise SceneError('Cannot find maya.exe to start a second session')

    try:
        subprocess.Popen([executable, '-file', str(path)], close_fds=True)
    except Exception as error:
        raise SceneError('Could not start a second Maya: %s' % error)
    return str(path)


def maya_executable():
    """Maya's own binary, not mayapy - sys.executable is the wrong one.

    Inside Maya, ``sys.executable`` is maya.exe on Windows but mayapy on some
    installs, and a second session started from mayapy would come up with no
    interface at all.
    """
    here = os.path.dirname(sys.executable)
    for name in ('maya.exe', 'maya', 'Maya.app'):
        candidate = os.path.join(here, name)
        if os.path.exists(candidate):
            return candidate

    try:
        location = os.environ.get('MAYA_LOCATION', '')
    except Exception:
        location = ''
    if location:
        for name in ('bin/maya.exe', 'bin/maya'):
            candidate = os.path.join(location, name)
            if os.path.exists(candidate):
                return candidate
    return ''


def save_open_scene():
    '''Save the scene that is open, if it has somewhere to be saved.'''
    cmds = _cmds()

    where = current_path()
    if not where:
        raise SceneError('This scene has never been saved, so there is '
                         'nowhere to save it to - use New Version instead')
    cmds.file(save=True, type=file_type(where), force=True)
    return where


def import_into_current(path):
    '''Import another scene's contents into the open one.

    A copy, not a link. Use ``reference_into_current`` for anything that
    should keep tracking the file it came from.
    '''
    cmds = _cmds()

    if not os.path.isfile(path):
        raise SceneError('File is gone: %s' % path)

    try:
        cmds.file(str(path), i=True, preserveReferences=True,
                  namespace=_namespace_for(path), mergeNamespacesOnClash=False)
    except Exception as error:
        raise SceneError('Could not import %s: %s' % (path, error))
    return str(path)


def reference_into_current(path):
    """Reference another scene into the open one.

    How an asset actually reaches a shot in Maya: the shot keeps a pointer,
    and the model's next version arrives by reloading rather than by
    reimporting. Namespaced after the file, because references sharing the
    root namespace collide the moment two of them contain a node with the
    same name.
    """
    cmds = _cmds()

    if not os.path.isfile(path):
        raise SceneError('File is gone: %s' % path)

    try:
        cmds.file(str(path), reference=True, namespace=_namespace_for(path))
    except Exception as error:
        raise SceneError('Could not reference %s: %s' % (path, error))
    return str(path)


def _namespace_for(path):
    '''A legal Maya namespace for a file, from its name without the version.'''
    stem = os.path.splitext(os.path.basename(str(path)))[0]
    cleaned = ''.join(char if (char.isalnum() or char == '_') else '_'
                      for char in stem)
    if not cleaned or cleaned[0].isdigit():
        cleaned = 'ref_%s' % cleaned
    return cleaned


def next_version(entity_context):
    '''``(path, version)`` for the next scene this task should get.'''
    if entity_context is None:
        raise SceneError('Pick a project, a group, an entity and a task first')

    config = session.config_for(entity_context)
    if not (config.paths.get('work_root') or '').strip():
        from BB_core import brief
        raise SceneError(brief.missing_root(
            session.project_of(entity_context), 'work root'))

    return workfiles.next_workfile(entity_context, session.DCC, config)


def create_version(entity_context, from_current=False):
    """Create the next version. Returns its path, or '' if the artist backed out.

    ``from_current`` keeps what is open and saves it under the new name;
    otherwise the scene is emptied first, which is what "New" means.
    """
    from . import scenesync

    cmds = _cmds()

    path, version = next_version(entity_context)
    os.makedirs(str(path.parent), exist_ok=True)

    if not from_current:
        try:
            cmds.file(new=True, force=True)
        except Exception as error:
            raise SceneError('Could not start a new scene: %s' % error)

    stamped = entity_context.at_version(version)
    stamp.write(stamped)
    note = scenesync.on_create(stamped)

    try:
        cmds.file(rename=str(path))
        cmds.file(save=True, type=file_type(path), force=True)
    except Exception as error:
        raise SceneError('Could not save %s: %s' % (path, error))

    state.context = stamped
    _store_thumb(stamped, version)
    forget_our_edit()
    return str(path), note


def save_next_version():
    '''Save the open scene as the next version of itself. Returns its path.'''
    cmds = _cmds()

    open_path = current_path()
    if not open_path:
        raise SceneError('Save this scene once before versioning it')

    entity_context, _source = stamp.read_current()
    if entity_context is None:
        raise SceneError('No Kitsu context on this scene')

    try:
        config = session.config_for(entity_context)
    except Exception:
        config = None

    try:
        path, version = versioning.bump(open_path, config)
    except ValueError:
        raise SceneError('This scene is not named to the pipeline scheme - '
                         'use the Kitsu browser to start one that is')

    stamped = entity_context.at_version(version)
    stamp.write(stamped)

    # Before the save, so the new version's output path is what gets written
    # into the file. Versioning up otherwise leaves the scene rendering into
    # the previous version's folder, which is worse than never having set it:
    # the frames land somewhere plausible and wrong.
    from . import scenesync
    note = scenesync.set_output(stamped)

    os.makedirs(str(path.parent), exist_ok=True)
    try:
        cmds.file(rename=str(path))
        cmds.file(save=True, type=file_type(path), force=True)
    except Exception as error:
        raise SceneError('Could not save %s: %s' % (path, error))

    state.context = stamped
    _store_thumb(stamped, version, config)
    forget_our_edit()
    return str(path), note


def _store_thumb(entity_context, version, config=None):
    """Write the picture the browser shows for this version.

    Taken here rather than read back from Kitsu because Kitsu has no notion
    of a work version: its preview files are numbered by a revision counter
    that counts publishes and review comments, so nothing there maps back to
    v007. Failing is not an error - the browser just shows a blank slot - but
    it is always reported, because a thumbnail that silently fails to write
    is indistinguishable from a version that has not been saved yet.
    """
    from . import capture

    # Derived here when the caller has none, so a path that forgets to pass
    # it cannot quietly write the picture under the default root instead of
    # the project's.
    if config is None:
        try:
            config = session.config_for(entity_context)
        except Exception:
            config = None

    picture, problem = capture.snapshot()
    if not picture:
        print('BB Kitsu Pipeline: no version thumbnail (%s)' % problem)
        return

    try:
        stored = workfiles.save_thumb(entity_context, session.DCC, picture,
                                      version, config)
        if stored is None:
            print('BB Kitsu Pipeline: could not store the version thumbnail')
    except Exception as error:
        print('BB Kitsu Pipeline: version thumbnail failed (%s)' % error)
    finally:
        capture.discard(picture)
