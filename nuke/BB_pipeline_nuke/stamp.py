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

KNOB = 'BB_pipeline'
LABEL = 'Kitsu context'


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
        knob = _root().knob(KNOB)
    except Exception:
        return None
    if knob is None:
        return None

    raw = knob.value()
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
