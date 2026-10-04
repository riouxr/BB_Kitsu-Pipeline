'''Storing the pipeline context inside the .joscomp project.

Nuke stamps a hidden knob on its root node; JOS has project metadata for
exactly this (``jos.set_metadata``): saved with the project, so reopening a
comp restores its project, sequence, shot, task and version - and the Kitsu
ids, which no filename can carry. Nothing in the UI shows it, so a stray edit
cannot quietly repoint a comp at another shot.
'''
import json
import os

KEY = 'BB_pipeline'


def write(entity_context):
    '''Stamp a context onto the project.'''
    import jos

    jos.set_metadata(KEY, json.dumps(entity_context.to_dict()))


def read():
    '''The stamped context, or None when this project has never been stamped.'''
    from BB_core.context import EntityContext

    try:
        import jos
        raw = jos.metadata(KEY)
    except Exception:
        return None

    if not raw:
        return None
    try:
        return EntityContext.from_dict(json.loads(raw))
    except (ValueError, TypeError):
        return None


def read_current():
    '''``(context, source)`` for the open project - stamp first, name second.'''
    import jos

    stamped = read()
    if stamped is not None:
        return stamped, 'stamp'

    from BB_core import settings
    from BB_core.context import EntityContext

    name = jos.project_path()

    # What the browser opened, when the project itself says nothing. Matched
    # on the folder so a context left over from another project cannot be
    # borrowed by this one.
    from . import session

    borrowed = _from_session(session.state.context, name)
    if borrowed is not None:
        return borrowed, 'session'

    if name:
        try:
            config = settings.config()
        except Exception:
            config = None
        # From the whole path, not just the name: the name carries the
        # entity and the version, and the folders carry the rest.
        recovered = EntityContext.from_path(name, config)
        if recovered is not None:
            return recovered, 'filename'

    return None, ''


def _from_session(remembered, name):
    """The browser's context, if the open project is the one it opened.

    Matched on the folder rather than the filename: the browser's context
    names a task, not a version, and the version is read off the file.
    """
    if remembered is None or not name:
        return None

    from BB_core import naming, workfiles

    from . import session

    try:
        config = session.config_for(remembered)
        folders = {os.path.normpath(str(workfiles.scene_dir(
                       remembered, session.DCC, config))).lower(),
                   os.path.normpath(str(workfiles.work_dir(
                       remembered, config))).lower()}
    except Exception:
        return None

    here = os.path.normpath(os.path.dirname(str(name))).lower()
    if here not in folders:
        return None

    try:
        version = naming.version_from_name(os.path.basename(str(name)), config)
    except Exception:
        version = None

    return remembered.at_version(version) if version else remembered
