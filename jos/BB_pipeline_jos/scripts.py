'''Opening, creating and versioning .joscomp projects.

The Nuke side's rules, through the ``jos`` module: a version number is
discovered once, by the core, from what is on disk - never typed in, never
inferred twice - and the project that gets saved carries the context that
named it.
'''
import os

from BB_core import workfiles

from . import session, stamp

state = session.state


class ScriptError(Exception):
    '''Something the artist needs to fix before this can work.'''


def _jos():
    import jos
    return jos


def is_modified():
    '''True when the project has unsaved changes.

    Every pipeline edit (the stamp, repointed Writes, the frame range) is
    followed by forget_our_edit(), so this is the artist's own work.
    '''
    return bool(_jos().modified())


def forget_our_edit():
    '''Clear the modified flag after a change only the pipeline made.'''
    try:
        _jos().set_modified(False)
    except Exception:
        pass


def current_path():
    return _jos().project_path()


def open_elsewhere(path):
    """Launch another JOS on *path*. Returns the path, or raises.

    Detached on purpose: the new JOS outlives whatever started it.
    """
    import subprocess

    executable = _jos().app_path()
    if not executable or not os.path.isfile(str(executable)):
        raise ScriptError('Cannot find JOS Compositor to start a second '
                          'session (%s)' % executable)
    try:
        subprocess.Popen([str(executable), str(path)], close_fds=True)
    except Exception as error:
        raise ScriptError('Could not start a second JOS: %s' % error)
    return path


def save_open_script():
    """Save the project that is open, if it has somewhere to be saved."""
    where = current_path()
    if not where:
        raise ScriptError('This project has never been saved, so there is '
                          'nowhere to save it to - use New Version instead')
    _jos().save()
    return where


def open_version(path, known=None):
    '''Open a .joscomp project in this session.

    Asking about unsaved work is the caller's job, so the flag goes down
    first: the app would ask again otherwise. ``known`` is the context the
    browser opened it from, for a project written before it was stamped.
    '''
    jos = _jos()

    if not os.path.isfile(path):
        raise ScriptError('File is gone: %s' % path)

    forget_our_edit()
    if not jos.open(str(path)):
        return ''
    state.context = stamp.read() or known
    forget_our_edit()
    return path


def import_into_current(path):
    """Paste another version's nodes into the open project (Nuke's nodePaste)."""
    if not os.path.isfile(path):
        raise ScriptError('File is gone: %s' % path)
    _jos().import_project(str(path))
    return path


def next_version(entity_context):
    '''``(path, version)`` for the next project this task should get.'''
    if entity_context is None:
        raise ScriptError('Pick a project, sequence, shot and task first')

    config = session.config_for(entity_context)
    if not (config.paths.get('work_root') or '').strip():
        from BB_core import brief
        raise ScriptError(brief.missing_root(
            session.project_of(entity_context), 'work root'))

    return workfiles.next_workfile(entity_context, session.DCC, config)


def create_version(entity_context, from_current=False):
    '''Create the next version. Returns its path.'''
    jos = _jos()

    path, version = next_version(entity_context)
    os.makedirs(str(path.parent), exist_ok=True)

    if not from_current:
        if is_modified() and not jos.ask(
                'The current project has unsaved changes.\n\n'
                'Start a new project anyway?'):
            return ''
        forget_our_edit()
        if not jos.new():
            return ''

    stamped = entity_context.at_version(version)
    stamp.write(stamped)
    _apply_format(stamped)

    jos.save(str(path))
    state.context = stamped
    _store_thumb(stamped, stamped.version)
    return str(path)


def _store_thumb(entity_context, version, config=None):
    """Write the picture the browser shows for this version.

    Kitsu has no notion of a work version, so the picture sits beside the
    version on disk. Failing is not an error - the browser shows a blank slot.
    """
    from . import capture

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


def read_sequence(pattern, first, last):
    """Create a Read node for a rendered sequence. Returns the node.

    The path arrives with a printf placeholder already in it, which JOS's Read
    takes as Nuke's does. The Master folder is a real copy of the flagged
    version (BB_core/master.py), so it reads like any other.
    """
    if not pattern:
        raise ScriptError('That render has no path')

    path = str(pattern).replace(chr(92), '/')
    try:
        read = _jos().create_node('Read', name='KitsuRead1', file=path,
                                  first=int(first), last=int(last))
    except Exception as error:
        raise ScriptError('Could not read %s: %s' % (path, error))
    _jos().refresh()
    return read


def upcoming_version():
    '''``(path, version)`` the next Save Next Version would write, or raises.'''
    if not current_path():
        raise ScriptError('Save this project once before versioning it')

    entity_context, _stamped_from = stamp.read_current()
    if entity_context is None:
        raise ScriptError('No Kitsu context on this project')

    try:
        config = session.config_for(entity_context)
    except Exception:
        config = None
    try:
        return workfiles.next_workfile(entity_context, session.DCC, config)
    except Exception as error:
        raise ScriptError(str(error))


def make_master(entity_context, path=None):
    '''Copy a saved version over the task's master project. Returns a note.'''
    from BB_core import master

    if entity_context is None:
        return 'no Kitsu context, so no master'
    try:
        made = master.set_master_scene(entity_context, session.DCC,
                                       session.config_for(entity_context),
                                       source=path)
    except Exception as error:
        return 'could not make master (%s)' % error
    return 'master is now %s (%s)' % (entity_context.versioned(), made.name)


def save_next_version():
    '''Save the open project as the next version of itself. Returns its path.'''
    jos = _jos()

    path, version = upcoming_version()
    entity_context, _stamped_from = stamp.read_current()
    try:
        config = session.config_for(entity_context)
    except Exception:
        config = None

    stamped = entity_context.at_version(version)
    stamp.write(stamped)
    state.context = stamped
    # Repointed before the save, so the saved version renders to its own
    # folder (Nuke repoints after and clears the flag; one save is simpler).
    _repoint_writes()

    os.makedirs(str(path.parent), exist_ok=True)
    jos.save(str(path))
    _store_thumb(stamped, version, config)
    forget_our_edit()
    return str(path)


def _repoint_writes():
    """Move every Kitsu Write onto the version that was just stamped."""
    from . import writenode

    moved = 0
    for node in _jos().all_nodes('Write'):
        if not writenode.is_ours(node):
            continue
        try:
            if writenode.set_output_path(node, quiet=True):
                moved += 1
        except Exception as error:
            print('BB Kitsu Pipeline: could not repoint %s (%s)'
                  % (node.name, error))
    return moved


def _apply_format(entity_context):
    '''Set the project's frame range and fps from Kitsu.

    Resolution is left alone, as on the Nuke side: a comp's format comes from
    its plate. The frame range is production data and Kitsu owns it.
    '''
    from BB_core import frames

    jos = _jos()
    shot = state.shot(entity_context.entity_id) or {}
    project = state.project(entity_context.project_id) or {}

    span = frames.frame_range(shot)
    if span:
        jos.set_frame_range(int(span[0]), int(span[1]))

    rate = frames.fps(project, shot)
    if rate:
        jos.set_fps(float(rate))
