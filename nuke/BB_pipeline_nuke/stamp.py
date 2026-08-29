'''Storing the pipeline context inside the .nk script.

Blender stamps a scene custom property. Nuke's equivalent is a knob on the
root node: knobs are saved with the script, so reopening a comp restores its
project, sequence, shot, task and version - and the Kitsu ids, which no
filename can carry.

The knob is hidden. It is machine state, not something to nudge in the
Project Settings, and a stray edit there would quietly repoint a comp at
another shot.
'''
import json
import os

KNOB = 'BB_pipeline'
LABEL = 'Kitsu context'

# Scripts stamped before the add-on was renamed. Read, never written: the
# ids in one of those knobs are still the right ids, and losing them would
# drop an old comp back to id-less filename parsing for no reason.
LEGACY_KNOBS = ('fake_pipeline',)


def _root():
    import nuke
    return nuke.root()


def write(entity_context):
    '''Stamp a context onto the script root.'''
    import nuke

    root = _root()
    knob = root.knob(KNOB)
    if knob is None:
        knob = nuke.String_Knob(KNOB, LABEL)
        knob.setFlag(nuke.INVISIBLE)
        root.addKnob(knob)

    knob.setValue(json.dumps(entity_context.to_dict()))
    return knob


def read():
    '''The stamped context, or None when this script has never been stamped.'''
    from BB_core.context import EntityContext

    try:
        root = _root()
    except Exception:
        return None

    raw = ''
    for name in (KNOB,) + LEGACY_KNOBS:
        knob = root.knob(name)
        if knob is None:
            continue
        raw = knob.value()
        if raw:
            break

    if not raw:
        return None
    try:
        return EntityContext.from_dict(json.loads(raw))
    except (ValueError, TypeError):
        return None


def read_current():
    '''``(context, source)`` for the open script - stamp first, name second.'''
    import nuke

    stamped = read()
    if stamped is not None:
        return stamped, 'stamp'

    from pathlib import Path

    from BB_core import settings
    from BB_core.context import EntityContext

    name = nuke.root().name()

    # What the browser opened, when the script itself says nothing. Matched
    # on the filename so a context left over from another script cannot be
    # borrowed by this one.
    from . import session

    remembered = session.state.context
    borrowed = _from_session(remembered, name)
    if borrowed is not None:
        return borrowed, 'session'

    if name and name != 'Root':
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
    """The browser's context, if the open script is the one it opened.

    Matched on the folder rather than the filename: the browser's context
    names a task, not a version, and the version is read off the file. That
    is also what stops a context left over from another script being
    borrowed by this one.
    """
    if remembered is None or not name:
        return None

    from BB_core import naming, workfiles

    from . import session

    try:
        folder = workfiles.work_dir(remembered,
                                    session.config_for(remembered))
    except Exception:
        return None

    here = os.path.normpath(os.path.dirname(str(name))).lower()
    if here != os.path.normpath(str(folder)).lower():
        return None

    try:
        config = session.config_for(remembered)
        version = naming.version_from_name(os.path.basename(str(name)), config)
    except Exception:
        version = None

    return remembered.at_version(version) if version else remembered
