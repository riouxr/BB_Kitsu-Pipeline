'''Rendering to the pipeline's own paths.

Three ways out of Blender, all filed the same way:

* **Image** - the current frame, for a look-dev check or a still.
* **Animation** - the frame range, EXR by default because that is what the
  rest of a VFX pipeline expects to be handed.
* **Playblast** - an OpenGL pass to a PNG sequence. Frames, not a movie:
  BB AnimViewer flips through frames, and the Kitsu Review panel already
  builds the H.264 for Kitsu out of them, so a movie written here could be
  neither viewed nor usefully re-published. The stream's ``ext`` decides, so
  a studio that wants a movie straight out can still set ``mp4``.

The output path is never typed in. It comes off the same
:class:`EntityContext` that named the scene file, so a render is always filed
under the version that produced it - the rule the whole pipeline hangs on.

Every render setting touched here is restored afterwards. A render that
quietly leaves the scene writing EXRs into a temp folder would be a nasty
thing to discover a week later - and the playblast did exactly that, the
other way round, for as long as it waited on Blender to say the render had
finished. See :func:`_run_playblast`.
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

# Extensions that mean one movie file rather than a folder of frames.
MOVIES = ('mp4', 'mov', 'mkv', 'avi')

# Blender's name for each still format a stream can ask for.
FORMATS = {'exr': 'OPEN_EXR', 'png': 'PNG', 'jpg': 'JPEG', 'jpeg': 'JPEG',
           'tif': 'TIFF', 'tiff': 'TIFF'}

# How often a running playblast is checked on.
WATCH_INTERVAL = 0.25

# Restored before everything else, in this order. Setting the format coerces
# depth and colour mode to suit it, so those have to go back after it or the
# coercion wins.
_FORMAT_FIRST = ('media_type', 'file_format', 'color_mode', 'color_depth')


class RenderSetup(Exception):
    '''The render cannot be set up - no context, no root, no stream.'''


def _image_state(image):
    """Every writable setting on an ImageFormatSettings, by name.

    Read off Blender's own description of the struct rather than kept as a
    list here. The list this replaced saved the file format and nothing else,
    and Blender quietly coerces bit depth and colour mode whenever the format
    changes - so an EXR that had been through a playblast came back half float
    instead of full float, or RGB instead of RGBA with its alpha gone from
    every render after. Measured, on 4.2 and 5.2 alike.
    """
    kept = {}
    for prop in image.bl_rna.properties:
        if (prop.identifier == 'rna_type' or prop.is_readonly
                or prop.type in ('POINTER', 'COLLECTION')):
            continue
        try:
            kept[prop.identifier] = getattr(image, prop.identifier)
        except Exception:
            continue
    return kept


def _put_back_image(image, kept):
    ordered = [name for name in _FORMAT_FIRST if name in kept]
    ordered += [name for name in kept if name not in _FORMAT_FIRST]
    for name in ordered:
        try:
            setattr(image, name, kept[name])
        except Exception:
            # Not valid for the format just restored - Blender offers some
            # settings only for some formats. Nothing to put back.
            continue


def _restore(scene, saved):
    render = scene.render

    render.filepath = saved['filepath']
    render.use_file_extension = saved['use_file_extension']
    render.use_overwrite = saved['use_overwrite']
    render.film_transparent = saved['film_transparent']
    _put_back_image(render.image_settings, saved['image'])
    for name, value in saved['ffmpeg'].items():
        try:
            setattr(render.ffmpeg, name, value)
        except Exception:
            continue


def _snapshot(scene):
    render = scene.render
    return {
        'filepath': render.filepath,
        'use_file_extension': render.use_file_extension,
        'use_overwrite': render.use_overwrite,
        'film_transparent': render.film_transparent,
        'image': _image_state(render.image_settings),
        'ffmpeg': {name: getattr(render.ffmpeg, name)
                   for name in ('format', 'codec', 'audio_codec')},
    }


def is_movie(settings):
    """True when a stream is written as one movie file rather than frames."""
    return (settings.get('ext') or '').lower() in MOVIES


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
        # A playblast is a look at the viewport, and a transparent film turns
        # the camera view's background black - in the frames, and in the
        # movie the Review panel builds from them. Version thumbnails went
        # black the same way until capture.py forced it off.
        render.film_transparent = False

    if kind == PLAYBLAST and is_movie(settings):
        # One movie file, for a studio that has asked for one.
        if hasattr(image, 'media_type'):
            image.media_type = 'VIDEO'
        image.file_format = 'FFMPEG'
        render.ffmpeg.format = 'MPEG4'
        render.ffmpeg.codec = 'H264'
        render.ffmpeg.audio_codec = 'NONE'
        render.use_file_extension = True
        render.filepath = str(directory / stem)
        return saved

    if kind == PLAYBLAST:
        # Frames, in whatever still format the stream names - PNG by default.
        if hasattr(image, 'media_type'):
            image.media_type = 'IMAGE'
        image.file_format = FORMATS.get(
            (settings.get('ext') or 'png').lower(), 'PNG')
        # Opaque, to match the film just set: an alpha channel here would only
        # ever hold the empty background.
        image.color_mode = 'RGB'
        render.use_file_extension = True
        render.filepath = str(directory / (stem + '.'))
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
        'movie': kind == PLAYBLAST and is_movie(settings),
    }

    # INVOKE_DEFAULT hands the render to a job thread and opens the render
    # window, which is what an artist wants. With no window - a farm, or a
    # test - there is nothing to invoke, so it runs inline and is restored
    # immediately.
    interactive = not bpy.app.background

    if kind == PLAYBLAST:
        _run_playblast(scene, saved, interactive)
        return '%s -> %s' % (stem, directory)

    if interactive:
        session.state.render_restore = (scene, saved)

    try:
        if kind == IMAGE:
            _call(bpy.ops.render.render, interactive, write_still=True)
        else:
            _call(bpy.ops.render.render, interactive, animation=True)
    finally:
        if not interactive:
            session.state.render_restore = None
            tidy_movie_name(session.state.last_render)
            _restore(scene, saved)

    return '%s -> %s' % (stem, directory)


def _run_playblast(scene, saved, interactive):
    """Render the playblast, and put the scene back once it is done.

    Not through the render handlers the other two kinds rely on. A viewport
    render fires none of them - render_init, pre, post, complete and cancel
    all stay silent, measured on every Blender from 4.2 to 5.2 - so the
    restore queued for it never ran. The scene stayed on H.264, and the
    snapshot sat waiting to be applied after whatever render came next: a
    Ctrl+F12 of the real sequence rendered with the playblast's settings,
    wrote a movie, and only then put the old settings back.

    A viewport render is a RENDER job from 4.5 on, true the moment it starts
    and false the moment it ends, so it is watched until then. The UI stays
    live and Esc still cancels - a cancel ends the job the same way.
    """
    if not interactive:
        # No window to invoke into - a farm, or a test. Inline, and restored
        # the moment it returns.
        try:
            bpy.ops.render.opengl(animation=True)
        finally:
            _finish_playblast(scene, saved)
        return

    bpy.ops.render.opengl('INVOKE_DEFAULT', animation=True)

    # False here means the job never started - refused, or over already -
    # and there is nothing to wait for.
    if not bpy.app.is_job_running('RENDER'):
        _finish_playblast(scene, saved)
        return

    def check():
        if bpy.app.is_job_running('RENDER'):
            return WATCH_INTERVAL
        _finish_playblast(scene, saved)
        return None

    # Not persistent: loading another file mid-playblast takes this scene
    # away, and with it anything there was to restore.
    bpy.app.timers.register(check, first_interval=WATCH_INTERVAL)


def _finish_playblast(scene, saved):
    tidy_movie_name(session.state.last_render)
    try:
        _restore(scene, saved)
    except ReferenceError:
        # The scene went away with a file load; nothing left to restore.
        pass


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


# -- where the newest frames are -----------------------------------------------

# What counts as a rendered frame when comparing folders by recency.
_FRAME_EXTENSIONS = ('.exr', '.png', '.jpg', '.jpeg', '.tif', '.tiff')


def _newest_frame(folder):
    """The modification time of the newest frame in *folder*, or -1."""
    newest = -1.0
    try:
        entries = os.scandir(folder)
    except OSError:
        return newest
    with entries:
        for entry in entries:
            if not entry.name.lower().endswith(_FRAME_EXTENSIONS):
                continue
            try:
                stamp_time = entry.stat().st_mtime
            except OSError:
                continue
            if stamp_time > newest:
                newest = stamp_time
    return newest


def _scene_output_folder(scene):
    path = bpy.path.abspath(scene.render.filepath or '')
    if not path:
        return ''
    if path.endswith(('/', chr(92))) or os.path.isdir(path):
        return path
    return os.path.dirname(path)


def latest_output(scene=None):
    """The folder holding the most recently rendered frames for this scene.

    The scene's own output path names one folder - the main render for the
    open version - and a viewer that only looks there cannot find a
    playblast, which this pipeline files beside the render rather than in
    it. So every stream and every version this task has rendered is a
    candidate, and so is whatever the output path names, and the folder
    whose newest frame is newest wins. A playblast just made beats last
    week's render; a version saved a moment ago with nothing rendered yet
    still finds the last thing that was.

    Read off the disk each time rather than remembered, so it is still right
    after a restart. Returns '' when there is nothing rendered anywhere.
    """
    scene = scene or bpy.context.scene
    if scene is None:
        return ''

    candidates = set()
    own = _scene_output_folder(scene)
    if own:
        candidates.add(os.path.normpath(own))

    entity_context = stamp.read(scene)
    if entity_context is None and scene == bpy.context.scene:
        entity_context = stamp.read_current()[0]

    if entity_context is not None and entity_context.is_complete():
        try:
            config = prefs.config()
            workfiles = session.workfiles_module
            for stream in config.streams:
                for _version, pattern, _first, _last in workfiles.render_versions(
                        entity_context, stream, config):
                    candidates.add(os.path.normpath(os.path.dirname(pattern)))
        except Exception as error:
            print('BB Kitsu Pipeline: could not list renders (%s)' % error)

    best, newest = '', -1.0
    for folder in candidates:
        stamp_time = _newest_frame(folder)
        if stamp_time > newest:
            best, newest = folder, stamp_time
    return best if newest >= 0 else ''


def _render_output_get(scene):
    # An RNA getter: whatever it raises is printed on every read and the
    # read returns nothing useful, so nothing is allowed to escape.
    try:
        return latest_output(scene)
    except Exception as error:
        print('BB Kitsu Pipeline: latest render lookup failed (%s)' % error)
        return ''


def register():
    for handler in (bpy.app.handlers.render_complete,
                    bpy.app.handlers.render_cancel):
        if _finished not in handler:
            handler.append(_finished)

    # Read-only and computed on every read, so it is never saved into the
    # .blend and never marks it modified. It is the one thing BB AnimViewer's
    # Open Render Output asks for, by name - no import either way, and a
    # viewer running without this add-on simply finds no such property and
    # falls back to the scene's output path.
    bpy.types.Scene.bb_render_output = bpy.props.StringProperty(
        name='Latest Render',
        description=('The folder holding the most recently rendered frames '
                     'for this scene - render or playblast, any version'),
        get=_render_output_get,
    )


def unregister():
    for handler in (bpy.app.handlers.render_complete,
                    bpy.app.handlers.render_cancel):
        if _finished in handler:
            handler.remove(_finished)

    if hasattr(bpy.types.Scene, 'bb_render_output'):
        del bpy.types.Scene.bb_render_output
