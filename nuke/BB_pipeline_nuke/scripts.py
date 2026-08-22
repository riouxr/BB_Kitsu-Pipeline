'''Opening, creating and versioning .nk scripts.

The same rule the Blender side follows: a version number is discovered once,
by the core, from what is on disk - never typed in, never inferred twice - and
the script that gets saved carries the context that named it.
'''
import os

from BB_core import settings, versioning, workfiles

from . import session, stamp

state = session.state


class ScriptError(Exception):
    '''Something the artist needs to fix before this can work.'''


def _nuke():
    import nuke
    return nuke


def is_modified():
    return bool(_nuke().modified())


def current_path():
    name = _nuke().root().name()
    return '' if not name or name == 'Root' else name


def open_version(path):
    '''Open a .nk script, refusing rather than discarding unsaved work.'''
    nuke = _nuke()

    if not os.path.isfile(path):
        raise ScriptError('File is gone: %s' % path)

    if is_modified():
        if not nuke.ask('The current script has unsaved changes.\n\n'
                        'Open %s anyway?' % os.path.basename(path)):
            return ''

    nuke.scriptOpen(path)
    state.context = stamp.read()
    return path


def import_into_current(path):
    """Paste another version's nodes into the open script.

    nodePaste is Nuke's own read-a-.nk-into-this-one, the same thing the
    paste shortcut does - so the nodes arrive selected and can be moved
    straight away, and nothing about the open script is replaced.
    """
    nuke = _nuke()

    if not os.path.isfile(path):
        raise ScriptError('File is gone: %s' % path)

    nuke.nodePaste(path)
    return path


def next_version(entity_context):
    '''``(path, version)`` for the next script this task should get.'''
    if entity_context is None:
        raise ScriptError('Pick a project, sequence, shot and task first')

    config = session.config_for(entity_context)
    if not (config.paths.get('work_root') or '').strip():
        raise ScriptError('Set a Work Root in Kitsu > Settings..., or a '
                          '[bb] block in the Kitsu project brief')

    return workfiles.next_workfile(entity_context, session.DCC, config)


def create_version(entity_context, from_current=False):
    '''Create the next version. Returns its path.'''
    nuke = _nuke()

    path, version = next_version(entity_context)
    os.makedirs(str(path.parent), exist_ok=True)

    if not from_current:
        if is_modified() and not nuke.ask(
                'The current script has unsaved changes.\n\n'
                'Start a new script anyway?'):
            return ''
        nuke.scriptClear()

    stamped = entity_context.at_version(version)
    stamp.write(stamped)
    _apply_format(stamped)

    nuke.scriptSaveAs(str(path), overwrite=1)
    state.context = stamped
    return str(path)


def save_next_version():
    '''Save the open script as the next version of itself. Returns its path.'''
    nuke = _nuke()

    open_path = current_path()
    if not open_path:
        raise ScriptError('Save this script once before versioning it')

    entity_context, _stamped_from = stamp.read_current()
    try:
        config = session.config_for(entity_context)
    except Exception:
        config = None

    try:
        path, version = versioning.bump(open_path, config)
    except ValueError:
        raise ScriptError('This script is not named to the pipeline scheme - '
                          'use the Kitsu browser to start one that is')

    if entity_context is None:
        raise ScriptError('No Kitsu context on this script')

    stamped = entity_context.at_version(version)
    stamp.write(stamped)

    os.makedirs(str(path.parent), exist_ok=True)
    nuke.scriptSaveAs(str(path), overwrite=1)
    state.context = stamped
    return str(path)


def _apply_format(entity_context):
    '''Set the script's frame range and fps from Kitsu.

    Resolution is deliberately left alone. Nuke's format is a named list and
    a comp's format usually comes from its plate, so writing one in would be
    the pipeline overriding a decision the artist made on purpose. The frame
    range is not like that - it is production data and Kitsu owns it.
    '''
    from BB_core import frames

    nuke = _nuke()
    shot = state.shot(entity_context.entity_id) or {}
    project = state.project(entity_context.project_id) or {}

    span = frames.frame_range(shot)
    if span:
        root = nuke.root()
        root.knob('first_frame').setValue(span[0])
        root.knob('last_frame').setValue(span[1])

    rate = frames.fps(project, shot)
    if rate:
        nuke.root().knob('fps').setValue(rate)
