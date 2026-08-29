'''Grabbing the viewport as a preview image.

An OpenGL render of the 3D view, not a screenshot: a screenshot would capture
whatever is floating over the viewport at the time - menus, the browser
dialog, gizmos - and Kitsu would end up with the add-on's own UI as the shot
thumbnail.

Every render setting touched here is put back afterwards. A preview must never
leave the scene configured differently than it was found, or the next real
render inherits a half-resolution PNG output.
'''
import os
import tempfile

import bpy

# Previews are thumbnails in Kitsu's UI; full resolution is wasted bytes and
# slow to upload over the office link.
DEFAULT_PERCENTAGE = 50


def _find_view3d(context):
    '''An area/region pair to render the user's own viewpoint from.

    Without one, render.opengl falls back to the scene camera - which is
    still a usable preview, just not the view that was on screen.
    '''
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for region in area.regions:
                if region.type == 'WINDOW':
                    return window, area, region
    return None, None, None


def viewport_png(context=None, percentage=DEFAULT_PERCENTAGE):
    '''Render the viewport to a temporary PNG. Returns the path, or None.

    Returns None rather than raising: a preview that cannot be captured -
    no GPU context in background mode, say - must not be the reason a save
    fails.
    '''
    context = context or bpy.context
    scene = context.scene
    if scene is None:
        return None

    render = scene.render
    image = render.image_settings

    handle, path = tempfile.mkstemp(prefix='bb_preview_', suffix='.png')
    os.close(handle)

    saved = {
        'filepath': render.filepath,
        'resolution_percentage': render.resolution_percentage,
        'use_file_extension': render.use_file_extension,
        'use_overwrite': render.use_overwrite,
        'film_transparent': render.film_transparent,
        'file_format': image.file_format,
        'color_mode': image.color_mode,
    }

    try:
        render.filepath = path
        render.resolution_percentage = int(percentage)
        render.use_file_extension = False
        render.use_overwrite = True

        # A shot set up to render with alpha - which is most of them - grabs
        # a transparent background here, and a transparent background written
        # to an RGB PNG is a black one. Free orbit views escape it because
        # film transparency only applies to the camera framing, so the same
        # scene gave a good thumbnail one minute and a black rectangle the
        # next, purely by having entered camera view.
        render.film_transparent = False

        image.file_format = 'PNG'
        image.color_mode = 'RGB'

        window, area, region = _find_view3d(context)
        if area is not None:
            with context.temp_override(window=window, area=area, region=region):
                bpy.ops.render.opengl(write_still=True, view_context=True)
        else:
            bpy.ops.render.opengl(write_still=True)
    except Exception as error:
        print('BB Kitsu Pipeline: could not capture a viewport preview (%s)' % error)
        _discard(path)
        return None
    finally:
        render.filepath = saved['filepath']
        render.resolution_percentage = saved['resolution_percentage']
        render.use_file_extension = saved['use_file_extension']
        render.use_overwrite = saved['use_overwrite']
        render.film_transparent = saved['film_transparent']
        image.file_format = saved['file_format']
        image.color_mode = saved['color_mode']

    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        _discard(path)
        return None

    return path


def _discard(path):
    try:
        os.remove(path)
    except OSError:
        pass


discard = _discard


class embedded_preview:
    '''Make sure the .blend gets a thumbnail when it is saved.

    Blender embeds one automatically - `file_preview_type` defaults to AUTO -
    but a user who has turned it off would get scene files that show as blank
    pages in the Append and Link browsers. Forced on for the pipeline's own
    saves only, and put straight back.
    '''

    def __enter__(self):
        self._paths = bpy.context.preferences.filepaths
        self._saved = self._paths.file_preview_type
        if self._saved == 'NONE':
            self._paths.file_preview_type = 'AUTO'
        return self

    def __exit__(self, *exception):
        self._paths.file_preview_type = self._saved
        return False


def generate_datablock_previews(context=None, limit=64):
    '''Render preview icons for the things worth appending out of this file.

    Scenes and collections only. They are what somebody picks in the Append
    or Link browser, there are rarely many of them, and generating a preview
    for every object in a heavy scene would turn a save into a coffee break.

    Best effort - it needs a GPU context and is skipped in background mode.
    Returns how many were generated.
    '''
    context = context or bpy.context
    if not hasattr(bpy.ops.ed, 'lib_id_generate_preview'):
        return 0

    targets = list(bpy.data.scenes) + list(bpy.data.collections)
    generated = 0

    for datablock in targets[:limit]:
        try:
            with context.temp_override(id=datablock):
                bpy.ops.ed.lib_id_generate_preview()
            generated += 1
        except Exception:
            # No GPU, or this ID type will not render one. Neither is worth
            # failing a save over.
            continue

    return generated
