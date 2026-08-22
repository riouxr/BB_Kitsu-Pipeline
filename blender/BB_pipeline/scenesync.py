'''Keeping the scene's frame range, frame rate and resolution honest.

Kitsu owns the shot's in and out frames and the project's frame rate and
resolution. Blender does not, so a scene created from the startup file starts
at 1-250, at whatever rate the startup file uses, at 1920x1080 - all three
wrong for a 3840x2160 show.

Two moments matter, and they are treated differently on purpose:

* **Creating** a version sets the range and rate outright. The scene is new,
  there is nothing to lose, and getting it right is the whole point.
* **Opening** one re-asks Kitsu and *reports* a difference rather than
  silently reaching into a scene somebody is working in. A shot's range does
  change mid-show, and finding out is useful - having your scene quietly
  retimed while you are animating is not. Preferences can turn that into an
  automatic fix for anyone who wants it.

Assets have no frame range. They do inherit the project resolution,
because a turntable that does not match the show is not much use.
'''
import bpy

from . import prefs, session


def kitsu_settings(entity_context, refresh=True):
    '''What Kitsu says this shot's range and rate should be.

    Returns a dict with ``frame_start``, ``frame_end``, ``fps`` and
    ``fps_base``, or None when there is nothing to say - an asset, a
    disconnected session, or a shot Kitsu has no frame data for.

    ``refresh`` re-reads the shot from the server. Opening a file does that,
    because the whole point is catching a range that changed since the file
    was saved; creating one uses the copy the browser already has.
    '''
    from BB_core import frames

    state = session.state
    if entity_context is None:
        return None
    if not state.connected or not entity_context.entity_id:
        return None

    is_shot = entity_context.entity_type == 'shot'

    shot = None
    if is_shot and refresh:
        try:
            shot = state.client.shot(entity_context.entity_id)
        except Exception:
            shot = None
    if not shot:
        shot = state.entity(entity_context.entity_id, not is_shot)
    if shot is None:
        shot = {}

    project = state.project(entity_context.project_id) or {}

    # Only a shot has a frame range; an asset takes the resolution alone.
    span = frames.frame_range(shot) if is_shot else None
    rate = frames.fps(project, shot)
    size = frames.resolution(project, shot)
    if span is None and rate is None and size is None:
        return None

    settings = {}
    if size is not None:
        settings['resolution_x'], settings['resolution_y'] = size
    if span is not None:
        settings['frame_start'], settings['frame_end'] = span
    if rate is not None:
        rational = frames.fps_to_rational(rate)
        if rational:
            settings['fps'], settings['fps_base'] = rational
            settings['fps_readable'] = frames.describe(rate)

    return settings or None


def differences(scene, settings):
    '''A list of human-readable differences between scene and Kitsu.'''
    if not scene or not settings:
        return []

    found = []
    if ('resolution_x' in settings
            and (scene.render.resolution_x, scene.render.resolution_y)
            != (settings['resolution_x'], settings['resolution_y'])):
        found.append('resolution %dx%d -> %dx%d'
                     % (scene.render.resolution_x, scene.render.resolution_y,
                        settings['resolution_x'], settings['resolution_y']))

    if 'frame_start' in settings and scene.frame_start != settings['frame_start']:
        found.append('start %d -> %d' % (scene.frame_start, settings['frame_start']))
    if 'frame_end' in settings and scene.frame_end != settings['frame_end']:
        found.append('end %d -> %d' % (scene.frame_end, settings['frame_end']))

    if 'fps' in settings:
        # Compared as a rate rather than as the pair, so 24/1.0 and 24/1.0
        # written differently do not read as a change.
        current = scene.render.fps / (scene.render.fps_base or 1.0)
        wanted = settings['fps'] / (settings.get('fps_base') or 1.0)
        if abs(current - wanted) > 0.0005:
            found.append('fps %.3f -> %s'
                         % (current, settings.get('fps_readable', wanted)))

    return found


def apply(scene, settings):
    '''Write Kitsu's range and rate onto the scene. Returns what changed.'''
    changed = differences(scene, settings)
    if not changed:
        return []

    if 'resolution_x' in settings:
        scene.render.resolution_x = settings['resolution_x']
        scene.render.resolution_y = settings['resolution_y']
    if 'frame_start' in settings:
        scene.frame_start = settings['frame_start']
    if 'frame_end' in settings:
        scene.frame_end = settings['frame_end']
    if 'fps' in settings:
        scene.render.fps = settings['fps']
        scene.render.fps_base = settings.get('fps_base', 1.0)

    return changed


def on_create(context, entity_context):
    '''Set the range and rate on a scene that is about to be saved.

    Returns a short note for the operator to report, or ''.
    '''
    preferences = prefs.get(context)
    if preferences is not None and not preferences.frame_range_on_create:
        return ''

    settings = kitsu_settings(entity_context, refresh=False)
    if not settings:
        return ''

    changed = apply(bpy.context.scene, settings)
    if not changed:
        return ''
    return 'set %s from Kitsu' % ', '.join(changed)


def on_open(context, entity_context):
    '''Check an opened scene against Kitsu. Returns a note, or ''.'''
    preferences = prefs.get(context)
    mode = preferences.frame_range_on_open if preferences else 'WARN'
    if mode == 'IGNORE':
        return ''

    settings = kitsu_settings(entity_context, refresh=True)
    if not settings:
        return ''

    scene = bpy.context.scene
    changed = differences(scene, settings)
    if not changed:
        return ''

    if mode == 'APPLY':
        apply(scene, settings)
        return 'Kitsu frame range applied: %s' % ', '.join(changed)

    return 'Kitsu disagrees with this scene: %s' % ', '.join(changed)


def pending(context=None):
    '''The differences the menu should be offering to fix, if any.'''
    from . import stamp

    entity_context = stamp.read_current()[0]
    settings = kitsu_settings(entity_context, refresh=False)
    if not settings:
        return []
    return differences(bpy.context.scene, settings)
