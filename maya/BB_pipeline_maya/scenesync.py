'''Keeping the scene's frame range, frame rate, resolution and output honest.

Kitsu owns the shot's in and out frames and the project's frame rate and
resolution. Maya does not, so a scene started from Maya's defaults begins at
1-200, at 24 fps, at 960x540 - all three wrong for most shows.

Two moments matter, and they are treated differently on purpose:

* **Creating** a version sets the range and rate outright. The scene is new,
  there is nothing to lose, and getting it right is the whole point.
* **Opening** one re-asks Kitsu and *reports* a difference rather than
  silently reaching into a scene somebody is working in. A shot's range does
  change mid-show, and finding out is useful - having your scene quietly
  retimed while you are animating is not.

Assets have no frame range. They do inherit the project resolution, because a
turntable that does not match the show is not much use.

The render output is not like either of those, and is re-applied every time.
Maya keeps it in the *workspace*, not the scene: the images file rule lives in
workspace.mel, so a scene reopened in a fresh Maya renders wherever that
workspace happens to point - usually the last shot somebody worked on. Aiming
it on every open is what stops frames landing somewhere plausible and wrong.
'''
import os

from BB_core import frames, settings, workfiles

from . import session

# Maya names the common rates and takes "<n>fps" for everything else. The
# named ones are used where they exist because that is what Maya itself
# writes back, and a scene that reads "film" rather than "24fps" is one an
# artist recognises.
_TIME_UNITS = {
    15.0: 'game',
    24.0: 'film',
    25.0: 'pal',
    30.0: 'ntsc',
    48.0: 'show',
    50.0: 'palf',
    60.0: 'ntscf',
}


def _cmds():
    import maya.cmds as cmds
    return cmds


def _flag(name, default=True):
    '''A behaviour switch out of the settings file.

    Maya has no add-on preferences panel the way Blender does, so these live
    beside the server and email rather than in a UI of their own.
    '''
    value = settings.get(name)
    return default if value is None else bool(value)


def time_unit(rate):
    '''Maya's name for a frame rate, or '' when there is nothing to set.'''
    if not rate:
        return ''
    for known, name in _TIME_UNITS.items():
        if abs(float(rate) - known) < 0.0005:
            return name
    # Maya 2022 and later accept an arbitrary rate spelled this way. Trimmed
    # of a trailing zero so 23.976 does not become 23.976000fps.
    text = ('%.3f' % float(rate)).rstrip('0').rstrip('.')
    return '%sfps' % text


def kitsu_settings(entity_context, refresh=True):
    '''What Kitsu says this entity's range, rate and resolution should be.

    Returns a dict, or None when there is nothing to say - a disconnected
    session, or an entity Kitsu has no frame data for.

    ``refresh`` re-reads the shot from the server. Opening a file does that,
    because the whole point is catching a range that changed since the file
    was saved; creating one uses the copy the browser already has.
    '''
    state = session.state
    if entity_context is None:
        return None
    if not state.connected or not entity_context.entity_id:
        return None

    is_shot = entity_context.entity_type == session.SHOT

    entity = None
    if is_shot and refresh:
        try:
            entity = state.client.shot(entity_context.entity_id)
        except Exception:
            entity = None
    if not entity:
        entity = state.entity(entity_context.entity_id, not is_shot)
    if entity is None:
        entity = {}

    project = state.project(entity_context.project_id) or {}

    # Only a shot has a frame range; an asset takes the resolution alone.
    span = frames.frame_range(entity) if is_shot else None
    rate = frames.fps(project, entity)
    size = frames.resolution(project, entity)
    if span is None and rate is None and size is None:
        return None

    wanted = {}
    if size is not None:
        wanted['resolution_x'], wanted['resolution_y'] = size
    if span is not None:
        wanted['frame_start'], wanted['frame_end'] = span
    if rate is not None:
        unit = time_unit(rate)
        if unit:
            wanted['time_unit'] = unit
            wanted['fps_readable'] = frames.describe(rate)

    return wanted or None


def scene_state():
    '''The scene's own range, rate and resolution, for comparison.'''
    cmds = _cmds()
    found = {}
    try:
        found['frame_start'] = int(cmds.playbackOptions(query=True, minTime=True))
        found['frame_end'] = int(cmds.playbackOptions(query=True, maxTime=True))
    except Exception:
        pass
    try:
        found['time_unit'] = cmds.currentUnit(query=True, time=True)
    except Exception:
        pass
    try:
        found['resolution_x'] = int(cmds.getAttr('defaultResolution.width'))
        found['resolution_y'] = int(cmds.getAttr('defaultResolution.height'))
    except Exception:
        pass
    return found


def differences(wanted, current=None):
    '''A list of human-readable differences between scene and Kitsu.'''
    if not wanted:
        return []
    if current is None:
        current = scene_state()

    found = []
    if ('resolution_x' in wanted and 'resolution_x' in current
            and (current['resolution_x'], current['resolution_y'])
            != (wanted['resolution_x'], wanted['resolution_y'])):
        found.append('resolution %dx%d -> %dx%d'
                     % (current['resolution_x'], current['resolution_y'],
                        wanted['resolution_x'], wanted['resolution_y']))

    if 'frame_start' in wanted and current.get('frame_start') != wanted['frame_start']:
        found.append('start %s -> %d'
                     % (current.get('frame_start'), wanted['frame_start']))
    if 'frame_end' in wanted and current.get('frame_end') != wanted['frame_end']:
        found.append('end %s -> %d'
                     % (current.get('frame_end'), wanted['frame_end']))

    if 'time_unit' in wanted and current.get('time_unit') != wanted['time_unit']:
        found.append('fps %s -> %s'
                     % (current.get('time_unit'),
                        wanted.get('fps_readable') or wanted['time_unit']))

    return found


def apply(wanted):
    '''Write Kitsu's range, rate and resolution onto the scene.

    Returns what changed. The time unit is set before the range, because
    changing it after would leave the playback range where it was in frames
    while meaning something different in seconds.
    '''
    changed = differences(wanted)
    if not changed:
        return []

    cmds = _cmds()

    if 'time_unit' in wanted:
        try:
            # updateAnimation=False: this is production data arriving, not a
            # retime. Letting Maya rescale the keys would move the animation
            # rather than reinterpret the rate.
            cmds.currentUnit(time=wanted['time_unit'], updateAnimation=False)
        except Exception:
            pass

    if 'frame_start' in wanted:
        start, end = wanted['frame_start'], wanted['frame_end']
        try:
            cmds.playbackOptions(animationStartTime=start, minTime=start,
                                 animationEndTime=end, maxTime=end)
        except Exception:
            pass
        # The render range as well as the playback range. They are separate
        # attributes in Maya, and a batch render reads the second one.
        for attribute, value in (('startFrame', start), ('endFrame', end)):
            try:
                cmds.setAttr('defaultRenderGlobals.%s' % attribute, value)
            except Exception:
                pass

    if 'resolution_x' in wanted:
        width, height = wanted['resolution_x'], wanted['resolution_y']
        try:
            cmds.setAttr('defaultResolution.width', width)
            cmds.setAttr('defaultResolution.height', height)
            cmds.setAttr('defaultResolution.pixelAspect', 1.0)
            # Left unset, the device aspect keeps its old value and the
            # render comes out correctly sized and wrongly shaped.
            cmds.setAttr('defaultResolution.deviceAspectRatio',
                         float(width) / float(height))
        except Exception:
            pass

    return changed


def output_path(entity_context):
    '''``(folder, stem)`` for this version's renders, or None.

    None when there is nothing to say - no context, or no render root
    configured yet. A missing root is not an error here: the browser reports
    that in its own words, and a scene should still open.
    '''
    if entity_context is None or not entity_context.is_complete():
        return None

    try:
        config = session.config_for(entity_context)
        folder = workfiles.render_dir(entity_context, 'main', config, 'maya')
        stem = workfiles.render_stem(entity_context, config)
    except Exception:
        return None
    if not folder or not stem:
        return None
    return str(folder), str(stem)


def set_output(entity_context):
    """Point Maya's render output at this version. Returns a note, or ''.

    Maya builds an output path out of two halves that live in different
    places: the workspace's ``images`` file rule, and the scene's
    ``imageFilePrefix``. Both are set, because setting either alone leaves
    the render landing half-right - the correct name under the last shot's
    folder, or this shot's folder under the previous version's name.
    """
    if not _flag('set_output_path', True):
        return ''

    aimed = output_path(entity_context)
    if not aimed:
        return ''
    folder, stem = aimed

    cmds = _cmds()
    # Maya wants forward slashes here; a Windows path with backslashes is
    # accepted and then re-emitted with the separators eaten.
    wanted = folder.replace(chr(92), '/')

    try:
        current = cmds.workspace(fileRuleEntry='images') or ''
    except Exception:
        current = ''
    try:
        prefix = cmds.getAttr('defaultRenderGlobals.imageFilePrefix') or ''
    except Exception:
        prefix = ''

    if (os.path.normpath(current).lower() == os.path.normpath(wanted).lower()
            and prefix == stem):
        return ''

    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        pass

    try:
        cmds.workspace(fileRule=('images', wanted))
        cmds.setAttr('defaultRenderGlobals.imageFilePrefix', stem, type='string')
        # Frames numbered name.####.ext, which is what every reader
        # downstream - including this pipeline's own render scan - expects.
        cmds.setAttr('defaultRenderGlobals.animation', True)
        cmds.setAttr('defaultRenderGlobals.putFrameBeforeExt', True)
        cmds.setAttr('defaultRenderGlobals.periodInExt', 1)
        cmds.setAttr('defaultRenderGlobals.extensionPadding', 4)
    except Exception as error:
        return 'could not set the output path (%s)' % error

    return 'output path -> %s' % folder


def on_create(entity_context):
    '''Set the range, rate and output on a scene about to be saved.

    Returns a short note for the caller to report, or ''.
    '''
    # Above the frame-range switch: that one is about the range, and a new
    # version still has to render somewhere sensible.
    output = set_output(entity_context)

    if not _flag('frame_range_on_create', True):
        return output

    wanted = kitsu_settings(entity_context, refresh=False)
    changed = apply(wanted) if wanted else []
    if output:
        changed = list(changed) + [output]

    if not changed:
        return ''
    return 'set %s from Kitsu' % ', '.join(changed)


def on_open(entity_context):
    """Check an opened scene against Kitsu. Returns a note, or ''."""
    # First, and outside every early return below. An asset carries no frame
    # range, so the Kitsu check has nothing to say about a prop - but a prop
    # still renders, and an output left pointing at the last shot is not a
    # disagreement to report, it is frames written into the wrong folder.
    output = set_output(entity_context)

    mode = str(settings.get('frame_range_on_open') or 'WARN').upper()
    if mode == 'IGNORE':
        return output

    wanted = kitsu_settings(entity_context, refresh=True)
    if not wanted:
        return output

    changed = differences(wanted)
    if not changed:
        return output

    if mode == 'APPLY':
        apply(wanted)
        return 'Kitsu frame range applied: %s' % ', '.join(changed)

    return 'Kitsu disagrees with this scene: %s' % ', '.join(changed)


def pending():
    '''The differences the menu should be offering to fix, if any.'''
    from . import stamp

    entity_context = stamp.read_current()[0]
    wanted = kitsu_settings(entity_context, refresh=False)
    if not wanted:
        return []
    return differences(wanted)
