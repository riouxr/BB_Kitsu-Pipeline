'''Rendering to the pipeline's own paths.

Three ways out of Blender, all filed the same way:

* **Image** - the current frame, for a look-dev check or a still.
* **Animation** - the frame range, EXR by default because that is what the
  rest of a VFX pipeline expects to be handed.
* **Playblast** - an OpenGL pass straight to H.264, because nothing
  downstream wants playblast frames and Kitsu re-encodes to H.264 anyway.

The output path is never typed in. It comes off the same
:class:`EntityContext` that named the scene file, so a render is always filed
under the version that produced it - the rule the whole pipeline hangs on.

Every render setting touched here is restored afterwards. A render that
quietly leaves the scene writing EXRs into a temp folder would be a nasty
thing to discover a week later.
'''
import os
from pathlib import Path

import bpy

from . import prefs, session, stamp

# What each menu entry renders, and which output stream it belongs to.
IMAGE = 'IMAGE'
ANIMATION = 'ANIMATION'
PLAYBLAST = 'PLAYBLAST'

STREAMS = {IMAGE: 'main', ANIMATION: 'main', PLAYBLAST: 'playblast'}


class RenderSetup(Exception):
    '''The render cannot be set up - no context, no root, no stream.'''


def _restore(scene, saved):
    render = scene.render
    image = render.image_settings

    render.filepath = saved['filepath']
    render.use_file_extension = saved['use_file_extension']
    render.use_overwrite = saved['use_overwrite']
    if 'media_type' in saved and hasattr(image, 'media_type'):
        image.media_type = saved['media_type']
    image.file_format = saved['file_format']
    if saved.get('codec') is not None:
        render.ffmpeg.codec = saved['codec']
        render.ffmpeg.format = saved['container']


def _snapshot(scene):
    render = scene.render
    image = render.image_settings
    saved = {
        'filepath': render.filepath,
        'use_file_extension': render.use_file_extension,
        'use_overwrite': render.use_overwrite,
        'file_format': image.file_format,
        'codec': render.ffmpeg.codec,
        'container': render.ffmpeg.format,
    }
    # Blender 5 splits stills from movies behind media_type; 4.x has no such
    # property and picks the format from file_format alone.
    if hasattr(image, 'media_type'):
        saved['media_type'] = image.media_type
    return saved


def target(context, kind):
    '''Where a render of this kind goes.

    Returns ``(entity_context, stream, directory, path_stem, settings)``.
    Raises :class:`RenderSetup` with something worth reading when it cannot
    be worked out.
    '''
    entity_context, _source = stamp.read_current()
    if entity_context is None or not entity_context.is_complete():
        raise RenderSetup('This scene carries no pipeline context - create a '
                          'version from the Kitsu browser first')
    if not entity_context.version:
        raise RenderSetup('This scene has no version to render under')

    stream = STREAMS[kind]

    try:
        config = prefs.config(context)
    except Exception as error:
        raise RenderSetup(str(error))

    if not (config.paths.get('render_root') or '').strip():
        raise RenderSetup('Set a Render Root in the add-on preferences, or a '
                          '[bb] block in the Kitsu project brief')

    workfiles = session.workfiles_module
    try:
        directory = workfiles.render_dir(entity_context, stream, config)
    except Exception as error:
        raise RenderSetup(str(error))

    settings = config.streams.get(stream, {})
    return (entity_context, stream, directory,
            workfiles.render_stem(entity_context, config), settings)


def _apply_output(scene, kind, directory, stem, settings):
    '''Point the scene at the pipeline path. Returns the snapshot to restore.'''
    saved = _snapshot(scene)
    render = scene.render
    image = render.image_settings

    os.makedirs(directory, exist_ok=True)
    render.use_overwrite = True

    if kind == PLAYBLAST:
        # One movie file, not a folder of frames.
        if hasattr(image, 'media_type'):
            image.media_type = 'VIDEO'
        image.file_format = 'FFMPEG'
        render.ffmpeg.format = 'MPEG4'
        render.ffmpeg.codec = 'H264'
        render.ffmpeg.audio_codec = 'NONE'
        render.use_file_extension = True
        render.filepath = str(directory / stem)
        return saved

    extension = (settings.get('ext') or 'exr').lower()
    if hasattr(image, 'media_type'):
        image.media_type = ('MULTI_LAYER_IMAGE' if extension == 'exr'
                            and image.file_format == 'OPEN_EXR_MULTILAYER'
                            else 'IMAGE')
    if extension == 'exr' and image.file_format not in ('OPEN_EXR',
                                                        'OPEN_EXR_MULTILAYER'):
        image.file_format = 'OPEN_EXR'

    render.use_file_extension = True

    if kind == IMAGE:
        # write_still writes the path verbatim - it does not append the frame
        # the way an animation render does - so the frame goes in by hand.
        # Without it a still lands as name_v003.exr, which does not match the
        # frame pattern and the review panel cannot find it.
        render.filepath = str(directory / ('%s.%04d' % (stem, scene.frame_current)))
    else:
        # An animation render appends the frame itself; the trailing dot gives
        # name_v003.0001.exr.
        render.filepath = str(directory / (stem + '.'))
    return saved


def run(context, kind):
    '''Set the output path, render, restore. Returns a note for the caller.'''
    entity_context, stream, directory, stem, settings = target(context, kind)

    scene = context.scene
    saved = _apply_output(scene, kind, directory, stem, settings)

    session.state.last_render = {
        'kind': kind,
        'stream': stream,
        'directory': str(directory),
        'stem': stem,
        'context': entity_context,
        'frame_start': scene.frame_start,
        'frame_end': scene.frame_end,
        'frame': scene.frame_current,
        'movie': kind == PLAYBLAST,
    }

    # INVOKE_DEFAULT hands the render to a job thread and opens the render
    # window, which is what an artist wants. With no window - a farm, or a
    # test - there is nothing to invoke, so it runs inline and is restored
    # immediately.
    interactive = not bpy.app.background

    if interactive:
        session.state.render_restore = (scene, saved)

    try:
        if kind == IMAGE:
            _call(bpy.ops.render.render, interactive, write_still=True)
        elif kind == ANIMATION:
            _call(bpy.ops.render.render, interactive, animation=True)
        else:
            _call(bpy.ops.render.opengl, interactive, animation=True)
    finally:
        if not interactive:
            session.state.render_restore = None
            tidy_movie_name(session.state.last_render)
            _restore(scene, saved)

    return '%s -> %s' % (stem, directory)


def _call(operator, interactive, **arguments):
    if interactive:
        return operator('INVOKE_DEFAULT', **arguments)
    return operator(**arguments)


# -- putting the scene back ----------------------------------------------------

def tidy_movie_name(last_render):
    """Rename a finished movie to the version it belongs to.

    Blender's ffmpeg writer always appends the frame range, and with no
    separator - a playblast of v001 lands as ``..._v0011001-1005.mp4``, which
    the naming scheme cannot parse. The range adds nothing a version folder
    does not already say, so it comes off.
    """
    if not last_render or not last_render.get('movie'):
        return

    directory = last_render.get('directory')
    stem = last_render.get('stem')
    if not directory or not stem or not os.path.isdir(directory):
        return

    wanted = os.path.join(directory, stem + '.mp4')
    if os.path.exists(wanted):
        return

    for name in sorted(os.listdir(directory)):
        base, extension = os.path.splitext(name)
        if extension.lower() != '.mp4' or base == stem:
            continue
        if not base.lower().startswith(stem.lower()):
            continue
        try:
            os.replace(os.path.join(directory, name), wanted)
        except OSError as error:
            print('BB Kitsu Pipeline: could not rename %s (%s)' % (name, error))
        return


def _capture_manual_render(scene, image=None):
    """Record and save a render the pipeline did not start.

    F12 renders into the Render Result buffer and writes nothing to disk, and
    nothing tells the add-on it happened - so a quick look could be rendered
    and then not published, because as far as the pipeline was concerned
    there was nothing to publish.

    The frame is written to the same place the pipeline's own Render Image
    would have put it, which is where the scene's output path already points,
    and recorded so the review panel can send it.

    Returns a note, or '' when there was nothing to do. Never raises: a
    render that has already finished must not be undone by a failure to
    file it.
    """
    preferences = prefs.get()
    if preferences is not None and not preferences.capture_manual_renders:
        return ''

    # The buffer by default; passed in only by the checks, which run in a
    # background Blender where the real Render Result carries no pixels.
    result = image if image is not None else bpy.data.images.get('Render Result')
    if result is None:
        return ''

    try:
        entity_context, stream, directory, stem, settings = target(
            bpy.context, IMAGE)
    except RenderSetup:
        # No pipeline context on this scene; an ordinary Blender render
        # that has nothing to do with us.
        return ''

    extension = (settings.get('ext') or 'exr').lower()
    frame = scene.frame_current if scene else bpy.context.scene.frame_current
    path = Path(directory) / ('%s.%04d.%s' % (stem, frame, extension))

    try:
        os.makedirs(str(directory), exist_ok=True)
        result.save_render(filepath=str(path), scene=scene)
    except Exception as error:
        print('BB Kitsu Pipeline: could not file the render (%s)' % error)
        return ''

    session.state.last_render = {
        'kind': IMAGE,
        'stream': stream,
        'directory': str(directory),
        'stem': stem,
        'context': entity_context,
        'frame_start': frame,
        'frame_end': frame,
        'frame': frame,
        'movie': False,
    }
    return str(path)


def _finished(*args):
    tidy_movie_name(session.state.last_render)

    pending = session.state.render_restore
    session.state.render_restore = None
    if not pending:
        # Nothing to restore means the pipeline did not start this render -
        # somebody pressed F12. That is still a render worth publishing.
        scene = args[0] if args else None
        filed = _capture_manual_render(scene)
        if filed:
            session.state.say('rendered %s' % os.path.basename(filed))
        return
    scene, saved = pending
    try:
        _restore(scene, saved)
    except ReferenceError:
        # The scene went away with a file load; nothing left to restore.
        pass


def register():
    for handler in (bpy.app.handlers.render_complete,
                    bpy.app.handlers.render_cancel):
        if _finished not in handler:
            handler.append(_finished)


def unregister():
    for handler in (bpy.app.handlers.render_complete,
                    bpy.app.handlers.render_cancel):
        if _finished in handler:
            handler.remove(_finished)
