'''Storing the pipeline context inside the Maya scene.

Blender stamps a scene custom property and Nuke a knob on the root. Maya's
equivalent is ``fileInfo``: arbitrary string key/values saved with the scene,
which survive a save and reopen in both .ma and .mb, and which in a .ma are
an ordinary greppable line of text.

**fileInfo escapes what you give it.** Setting a value applies C-style
escaping immediately - a quote comes back as ``\\"``, a backslash as ``\\\\``,
a newline as a literal ``\\n`` - and the escaped form is what every later
read returns, in memory and after a reopen alike. Measured, not assumed: a
295-character JSON stamp reads back as 336 characters.

Two consequences shape this module:

* reading has to unescape before parsing, and
* a value that is read and written back would be escaped a second time, so
  nothing here ever re-stores what it just read.

``json.dumps`` is left at its default ``ensure_ascii=True`` on purpose. That
makes the payload pure ASCII whose only escapable characters are the quote
and the backslash, so a project named in any language survives the round trip
without this module having to care.
'''
import json
import os

KEY = 'BB_pipeline'

# What fileInfo turns each character into, and therefore what has to be undone
# on the way back. Ordered so the backslash rule is applied by the scanner
# rather than by a chain of replaces, which would turn a literal \\n into a
# newline.
_UNESCAPE = {
    '"': '"',
    chr(92): chr(92),
    'n': chr(10),
    't': chr(9),
    'r': chr(13),
    'b': chr(8),
    'f': chr(12),
}


def _cmds():
    import maya.cmds as cmds
    return cmds


def _unescape(text):
    '''Undo the C-style escaping fileInfo applies when a value is set.

    Scanned rather than chained through ``str.replace``: replacing ``\\\\``
    first and ``\\n`` second turns a genuine backslash-n in the data into a
    newline, and doing it the other way round is worse. One pass, deciding
    each escape by the character that follows it.
    '''
    text = str(text or '')
    if chr(92) not in text:
        return text

    out = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char != chr(92) or index + 1 >= length:
            out.append(char)
            index += 1
            continue

        following = text[index + 1]
        if following in _UNESCAPE:
            out.append(_UNESCAPE[following])
            index += 2
        else:
            # Not an escape this module produced - a \\u sequence from
            # json.dumps, say. Left exactly as it is so json can decode it.
            out.append(char)
            index += 1
    return ''.join(out)


def write(entity_context):
    '''Stamp a context onto the open scene. Returns the JSON written.'''
    cmds = _cmds()

    blob = json.dumps(entity_context.to_dict())
    cmds.fileInfo(KEY, blob)
    return blob


def clear():
    '''Remove the stamp, for a scene that should not claim to be a version.'''
    cmds = _cmds()
    try:
        cmds.fileInfo(remove=KEY)
    except Exception:
        # Maya raises rather than shrugging when the key was never set.
        pass


def raw():
    '''The stamp exactly as fileInfo returns it, escaping and all, or ''.'''
    try:
        found = _cmds().fileInfo(KEY, query=True)
    except Exception:
        return ''
    return found[0] if found else ''


def read():
    '''The stamped context, or None when this scene has never been stamped.'''
    from BB_core.context import EntityContext

    stored = raw()
    if not stored:
        return None

    # Parsed as-is first. A Maya that did not escape the value - or a stamp
    # written by something other than fileInfo - is then read without the
    # unescaping having a chance to damage it.
    for candidate in (stored, _unescape(stored)):
        try:
            return EntityContext.from_dict(json.loads(candidate))
        except (ValueError, TypeError):
            continue
    return None


def current_path():
    '''The scene's path, or '' for one that has never been saved.'''
    cmds = _cmds()
    try:
        name = cmds.file(query=True, sceneName=True) or ''
    except Exception:
        return ''
    return str(name)


def read_current():
    '''``(context, source)`` for the open scene - stamp first, name second.'''
    stamped = read()
    if stamped is not None:
        return stamped, 'stamp'

    from BB_core import settings
    from BB_core.context import EntityContext

    from . import session

    name = current_path()

    # What the browser opened, when the scene itself says nothing. Matched on
    # the folder so a context left over from another scene cannot be borrowed
    # by this one.
    borrowed = _from_session(session.state.context, name)
    if borrowed is not None:
        return borrowed, 'session'

    if name:
        try:
            config = session.config_for(None)
        except Exception:
            try:
                config = settings.config()
            except Exception:
                config = None
        # From the whole path, not just the name: the name carries the entity
        # and the version, and the folders carry the rest.
        recovered = EntityContext.from_path(name, config)
        if recovered is not None:
            return recovered, 'filename'

    return None, ''


def _from_session(remembered, name):
    '''The browser's context, if the open scene is the one it opened.

    Matched on the folder rather than the filename: the browser's context
    names a task, not a version, and the version is read off the file. That
    is also what stops a context left over from another scene being borrowed
    by this one.
    '''
    if remembered is None or not name:
        return None

    from BB_core import naming, workfiles

    from . import session

    try:
        folder = workfiles.work_dir(remembered, session.config_for(remembered))
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
