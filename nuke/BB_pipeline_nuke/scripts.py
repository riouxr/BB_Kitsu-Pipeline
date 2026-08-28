'''Opening, creating and versioning .nk scripts.

The same rule the Blender side follows: a version number is discovered once,
by the core, from what is on disk - never typed in, never inferred twice - and
the script that gets saved carries the context that named it.
'''
import os
import sys

from BB_core import settings, versioning, workfiles

from . import session, stamp

state = session.state


class ScriptError(Exception):
    '''Something the artist needs to fix before this can work.'''


def _nuke():
    import nuke
    return nuke


def is_modified():
    '''True when the *artist* has unsaved changes.

    Not the same as nuke.modified(). This add-on stamps the root, repoints
    Write nodes and applies the frame range - all of which set the flag
    without anybody having touched the comp. Asking "save your changes?"
    about those is asking about work nobody did, so each of them clears the
    flag again once it has finished.
    '''
    return bool(_nuke().modified())


def forget_our_edit():
    '''Clear the modified flag after a change only the pipeline made.

    Called where the script has just been written to disk, or where the edit
    is bookkeeping the artist neither made nor needs to save.
    '''
    nuke = _nuke()
    try:
        nuke.root().setModified(False)
    except Exception:
        # Older Nukes, or a Root that has gone away with a file load.
        pass


def current_path():
    name = _nuke().root().name()
    return '' if not name or name == 'Root' else name


def open_elsewhere(path):
    """Launch another Nuke on *path*. Returns the path, or '' if it could not.

    A second session rather than replacing this one, for the times a comp is
    still wanted on screen while another is opened. Detached on purpose: the
    new Nuke outlives whatever started it.
    """
    import subprocess

    nuke = _nuke()

    executable = getattr(nuke, 'EXE_PATH', '') or sys.executable
    if not executable or not os.path.isfile(str(executable)):
        raise ScriptError('Cannot find Nuke to start a second session (%s)'
                          % executable)

    try:
        subprocess.Popen([str(executable), str(path)], close_fds=True)
    except Exception as error:
        raise ScriptError('Could not start a second Nuke: %s' % error)
    return path


def save_open_script():
    """Save the script that is open, if it has somewhere to be saved."""
    nuke = _nuke()

    where = current_path()
    if not where:
        raise ScriptError('This script has never been saved, so there is '
                          'nowhere to save it to - use New Version instead')
    nuke.scriptSave(where)
    return where

def open_version(path, known=None):
    '''Open a .nk script in this session.

    Asking about unsaved work is the caller's job. This add-on stamps the
    root and repoints Write nodes, so nuke.modified() is true after any
    pipeline action whether or not the artist touched anything - and the
    prompt that used to live here fired on scripts nobody had changed.

    ``known`` is the context the browser opened it from. A script written
    before it was stamped, or by hand, carries nothing to identify it - and
    throwing away what the browser already knew is how a file opened from
    the browser ends up unable to say which project it is in.
    '''
    nuke = _nuke()

    if not os.path.isfile(path):
        raise ScriptError('File is gone: %s' % path)

    nuke.scriptOpen(path)
    state.context = stamp.read() or known
    # A freshly opened script has no unsaved changes, whatever reading it
    # may have touched.
    forget_our_edit()
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


def _root_problem(entity_context):
    """Why a configured root did not arrive, or '' when nothing is wrong."""
    from BB_core import brief

    try:
        project = state.project((entity_context or {}).project_id)
    except Exception:
        project = None
    return brief.problem(project)


def next_version(entity_context):
    '''``(path, version)`` for the next script this task should get.'''
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
    _store_thumb(stamped, stamped.version)
    return str(path)


def _store_thumb(entity_context, version, config=None):
    """Write the picture the browser shows for this version.

    Taken here rather than read back from Kitsu because Kitsu has no notion
    of a work version: its preview files are numbered by a revision counter
    that counts publishes and review comments, so nothing there maps back to
    v007. Failing is not an error - the browser just shows a blank slot.
    """
    from . import capture

    nuke = _nuke()

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
        nuke.tprint('BB Kitsu Pipeline: no version thumbnail (%s)' % problem)
        return

    # Reported rather than swallowed. A thumbnail that silently fails to
    # write is indistinguishable from a version that simply has not been
    # saved yet.
    try:
        stored = workfiles.save_thumb(entity_context, 'nuke', picture, version,
                                      config)
        if stored is None:
            nuke.tprint('BB Kitsu Pipeline: could not store the version '
                        'thumbnail')
    except Exception as error:
        nuke.tprint('BB Kitsu Pipeline: version thumbnail failed (%s)' % error)
    finally:
        capture.discard(picture)


def read_sequence(pattern, first, last):
    """Create a Read node for a rendered sequence. Returns the node.

    The path arrives with a printf placeholder already in it, which is what
    Nuke's Read wants - handing it a single frame would give a one-frame
    Read that silently ignores the rest of the sequence.
    """
    nuke = _nuke()

    if not pattern:
        raise ScriptError('That render has no path')

    path = str(pattern).replace(chr(92), '/')
    try:
        read = nuke.nodes.Read(file=path, first=int(first), last=int(last),
                               origfirst=int(first), origlast=int(last))
    except Exception as error:
        raise ScriptError('Could not read %s: %s' % (path, error))

    try:
        read.setName('KitsuRead1', uncollide=True)
    except Exception:
        pass
    return read


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
    _repoint_writes()
    _store_thumb(stamped, version, config)
    # Repointing happens after the save, so without this the freshly saved
    # script reports unsaved changes it does not have.
    forget_our_edit()
    return str(path)


def _repoint_writes():
    """Move every Kitsu Write onto the version that was just created.

    Versioning up otherwise leaves the Writes aimed at the previous
    version's folder, which is worse than never having set them: the frames
    land somewhere plausible and wrong, and the first anybody knows is a
    comp reading a render that belongs to an older script.
    """
    nuke = _nuke()

    from . import writenode

    moved = 0
    try:
        nodes = nuke.allNodes('Write')
    except Exception:
        return 0

    for node in nodes:
        if not writenode.is_ours(node):
            continue
        try:
            if writenode.set_output_path(node):
                moved += 1
        except Exception as error:
            nuke.tprint('BB Kitsu Pipeline: could not repoint %s (%s)'
                        % (node.name(), error))
    return moved


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
