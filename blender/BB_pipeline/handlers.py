'''Keeping the browser in step with the file that is open.

Loading a .blend replaces the WindowManager, which takes the browser's four
selectors with it. Rather than leaving the panel blank after every Open, the
load handler reads the context stamped into the scene and puts the selectors
back - using the shot lists already cached in the session, so restoring costs
no requests at all.
'''
import bpy
from bpy.app.handlers import persistent

from . import fetch, prefs, properties, scenesync, session, stamp


def _restore_project_bookmark(context=None):
    """Put the selector back on the last project deliberately chosen."""
    state = session.state
    props = properties.get(context)

    preferences = prefs.get(context)
    wanted = getattr(preferences, 'last_project', '') if preferences else ''
    if not wanted or props.project == wanted:
        return

    if not any(project['id'] == wanted for project in state.projects):
        return

    with properties.suspend_updates():
        try:
            props.project = wanted
        except TypeError:
            pass


def restore_selection(entity_context, context=None):
    '''Point the selectors at a context. True if every level landed.

    Needed after anything that replaces the WindowManager - opening a file,
    but also reading the startup file when creating a new version - because
    the selectors live on the WindowManager and go with it.

    Anything the session has not cached is skipped rather than guessed: a
    half-restored selection that looks right but points at the wrong shot
    would be worse than an empty one.
    '''
    state = session.state
    props = properties.get(context)
    is_asset = entity_context.entity_type == 'asset'

    if not any(project['id'] == entity_context.project_id for project in state.projects):
        # The file cannot say which project it belongs to - it was never
        # stamped, and the path no longer names one. Loading it has just
        # reset the selector to the first project in the list, which is a
        # wrong answer that looks like a real one: everything downstream
        # then reads that show's brief, finds no roots, and sets no render
        # path. The bookmark is the last project actually chosen, so it is
        # a better answer than whatever happens to sort first.
        _restore_project_bookmark(context)
        return False

    groups = state.asset_types if is_asset else state.sequences
    entities = state.assets if is_asset else state.shots

    with properties.suspend_updates():
        props.project = entity_context.project_id
        props.entity_type = 'ASSET' if is_asset else 'SHOT'

        if not any(g['id'] == entity_context.group_id for g in groups):
            return False
        setattr(props, 'asset_type' if is_asset else 'sequence',
                entity_context.group_id)

        if not any(e['id'] == entity_context.entity_id for e in entities):
            return False
        setattr(props, 'asset' if is_asset else 'shot', entity_context.entity_id)

        if not any(t['id'] == entity_context.task_id for t in state.tasks):
            return False
        props.task = entity_context.task_id

    return True


@persistent
def on_load(_dummy):
    state = session.state

    # A render belongs to the file it came out of. Left standing across a
    # file load it is still offered for publishing, and publishes the
    # previous asset's picture against the one now open.
    state.last_render = None
    state.render_restore = None

    # The selectors live on the WindowManager the load has just replaced, so
    # the project has silently reset to whichever one sorts first. Put the
    # bookmark back before reading anything: recovering a context from a
    # filename is done against the project's own naming templates, and the
    # wrong project supplies the wrong ones - which is how a file sitting in
    # the middle of the pipeline came to read as being outside it. A stamped
    # file still overrides this below; the bookmark is only the starting
    # point, not the answer.
    _restore_project_bookmark()

    entity_context, source = stamp.read_current()
    state.context = entity_context

    if entity_context is None:
        return

    if not state.connected:
        # A Launch-opened file has never connected yet, and the background
        # auto-connect timer racing this handler is what left the browser's
        # selectors pointed at the wrong project before - connecting here,
        # inline, is the same call the Connect menu item makes, just run the
        # moment the file is open instead of hoping a worker thread gets
        # there first.
        from . import autoconnect
        autoconnect.connect_now(background=False)

    if not state.connected:
        state.say('opened %s - connect to Kitsu to publish it'
                  % entity_context.versioned())
        return

    restored = restore_selection(entity_context)

    # The file list is worth rebuilding either way: it reads the disk, not the
    # server, so it works even when the selectors could not be restored.
    fetch.refresh_workfiles()

    # Re-ask Kitsu about the frame range: a shot's in and out do move, and
    # the whole point of looking is catching that it happened.
    framing = scenesync.on_open(None, entity_context)

    if framing:
        state.say(framing, error='disagrees' in framing)
    elif restored:
        state.say('opened %s' % entity_context.versioned())
    elif source == 'filename':
        state.say('opened %s - no stamped context, pick the task to publish'
                  % entity_context.versioned())
    else:
        state.say('opened %s - reselect the project to reload its shots'
                  % entity_context.versioned())


def register():
    if on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_load)


def unregister():
    if on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load)
