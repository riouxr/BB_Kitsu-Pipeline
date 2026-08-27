'''Turning a render into something Kitsu can play.

VFX renders to EXR, and Kitsu cannot show an EXR. So submitting a render
builds an H.264 MP4 from the frames on disk and uploads that, leaving the EXRs
where they are.

H.264 specifically, and the reason is measured rather than assumed: uploading
an H.265 file to the studio's Zou came back re-encoded to ``avc1`` anyway, and
its normalised original was *larger* than the H.264 one. Kitsu transcodes
everything to H.264 for browser playback, so encoding to anything else costs
time and adds a decoder dependency for no gain.

The movie is built through the sequencer rather than by shelling out to
ffmpeg. Blender is already here, it already knows the scene's view transform,
and getting that wrong is how a linear EXR ends up on Kitsu looking washed
out.
'''
import os
import re
import tempfile

import bpy

from . import prefs, session

# Frames written by the pipeline are <stem>.<frame>.<ext>.
FRAME = re.compile(r"^(?P<stem>.+)\.(?P<frame>\d+)\.(?P<ext>[A-Za-z0-9]+)$")

# What Kitsu will accept as-is. Anything else gets converted.
#
# WebP is deliberately absent: Zou 1.0.26 rejects it outright with HTTP 400,
# so uploading one is a failure rather than a saving.
PLAYABLE = {"mp4", "mov", "png", "jpg", "jpeg", "webm", "gif"}

LINEAR = "Linear Rec.709"


def review_size(context, width, height):
    """The size to upload at - full resolution unless a cap is configured.

    Full size by default, and for a stronger reason than taste. Kitsu already
    keeps a low version of every movie alongside the original - it builds the
    proxy itself - and Zou conforms whatever it is given to the project
    resolution, *upscaling* anything smaller. Sending a scaled-down movie
    therefore saves nothing on the server and replaces the high-quality
    version with a soft upscale of a small one.

    The cap exists for a slow link, not as a default.

    Both dimensions come back even: H.264 in yuv420p cannot encode an odd
    one, and an off-by-one there fails the encode rather than the frame.
    """
    preferences = prefs.get(context)
    limit = getattr(preferences, "review_max_width", 0) if preferences else 0

    if not limit or width <= limit or width <= 0:
        return _even(width), _even(height)

    scale = float(limit) / float(width)
    return _even(limit), _even(int(round(height * scale)))


def _even(value):
    value = max(2, int(value))
    return value if value % 2 == 0 else value + 1


def frames_on_disk(last_render):
    '''Every frame file the last render produced, in order.'''
    if not last_render:
        return []

    directory = last_render.get("directory")
    stem = last_render.get("stem")
    if not directory or not stem or not os.path.isdir(directory):
        return []

    found = []
    for name in os.listdir(directory):
        match = FRAME.match(name)
        if match and match.group("stem").lower() == stem.lower():
            found.append((int(match.group("frame")),
                          os.path.join(directory, name)))

    if found:
        return [path for _frame, path in sorted(found)]

    # One file rather than a sequence: a playblast movie, or a still. Any
    # extension, because a still render is an EXR as often as a PNG and
    # refusing to find it is how "nothing rendered to submit" happens.
    for name in sorted(os.listdir(directory)):
        base, extension = os.path.splitext(name)
        if extension and base.lower().startswith(stem.lower()):
            return [os.path.join(directory, name)]

    return []


def _match_colour(target, source):
    '''Copy the source scene's colour management onto the review scene.

    Without this a linear EXR is written out as if it were already display
    referred, and the review looks nothing like the render.
    '''
    for attribute in ("view_transform", "look", "exposure", "gamma"):
        try:
            setattr(target.view_settings, attribute,
                    getattr(source.view_settings, attribute))
        except Exception:
            pass
    try:
        target.display_settings.display_device = source.display_settings.display_device
    except Exception:
        pass


def build_movie(context, last_render, files):
    '''Render the frames to an H.264 MP4. Returns its path, or None.'''
    if not files:
        return None

    source = context.scene
    handle, output = tempfile.mkstemp(prefix="bb_review_", suffix=".mp4")
    os.close(handle)
    os.remove(output)          # Blender writes it; it must not exist first.
    stem = os.path.splitext(output)[0]

    scene = bpy.data.scenes.new("BB review")
    try:
        width, height = review_size(context, source.render.resolution_x,
                                    source.render.resolution_y)
        scene.render.resolution_x = width
        scene.render.resolution_y = height
        scene.render.resolution_percentage = 100
        scene.render.fps = source.render.fps
        scene.render.fps_base = source.render.fps_base
        scene.frame_start = 1
        scene.frame_end = len(files)
        _match_colour(scene, source)

        scene.sequence_editor_create()
        strip = scene.sequence_editor.strips.new_image(
            name="review", filepath=files[0], channel=1, frame_start=1)
        for path in files[1:]:
            strip.elements.append(os.path.basename(path))

        # EXRs are scene linear; saying so is what lets the view transform do
        # its job instead of the footage being read as already graded.
        if files[0].lower().endswith(".exr"):
            try:
                strip.colorspace_settings.name = LINEAR
            except Exception:
                pass

        image = scene.render.image_settings
        if hasattr(image, "media_type"):
            image.media_type = 'VIDEO'
        image.file_format = 'FFMPEG'
        scene.render.ffmpeg.format = 'MPEG4'
        scene.render.ffmpeg.codec = 'H264'
        scene.render.ffmpeg.constant_rate_factor = 'HIGH'
        scene.render.ffmpeg.ffmpeg_preset = 'GOOD'
        scene.render.ffmpeg.audio_codec = 'NONE'
        scene.render.use_file_extension = True
        scene.render.filepath = stem

        bpy.ops.render.render(animation=True, scene=scene.name)
    finally:
        bpy.data.scenes.remove(scene)

    # use_file_extension gives <stem><start>-<end>.mp4.
    folder = os.path.dirname(stem)
    prefix = os.path.basename(stem)
    for name in sorted(os.listdir(folder)):
        if name.startswith(prefix) and name.lower().endswith(".mp4"):
            return os.path.join(folder, name)
    return None


def convert_still(context, path):
    """Write a single frame out in a format Kitsu can show. Returns its path.

    Through ``save_render`` rather than a straight copy, so the scene's view
    transform is applied - an EXR handed over untouched would arrive on Kitsu
    as the raw linear data and look washed out.

    PNG or JPEG, from preferences, and PNG is the better answer for a reason
    that is the opposite of the obvious one: Zou re-encodes every still to PNG
    on ingest, so a JPEG upload is stored as a PNG made from lossy data -
    which compresses *worse*. Measured on the studio server, a 78 KB JPEG
    became a 218 KB PNG while a 118 KB PNG became a 90 KB one. JPEG only ever
    saves upload bandwidth, never storage, and costs quality to do it.
    """
    source = context.scene
    preferences = prefs.get(context)
    wanted = getattr(preferences, "still_format", "PNG") if preferences else "PNG"
    quality = getattr(preferences, "still_quality", 90) if preferences else 90

    suffix = ".jpg" if wanted == "JPEG" else ".png"
    scene = bpy.data.scenes.new("BB still")
    image = None

    handle, output = tempfile.mkstemp(prefix="bb_review_", suffix=suffix)
    os.close(handle)

    try:
        _match_colour(scene, source)
        settings = scene.render.image_settings
        if hasattr(settings, "media_type"):
            settings.media_type = 'IMAGE'
        settings.file_format = wanted
        settings.color_mode = 'RGB'
        if wanted == 'JPEG':
            settings.quality = int(quality)

        image = bpy.data.images.load(path)

        width, height = review_size(context, image.size[0], image.size[1])
        if (width, height) != tuple(image.size):
            image.scale(width, height)

        image.save_render(output, scene=scene)
    except Exception as error:
        print("BB Kitsu Pipeline: could not convert %s (%s)" % (path, error))
        try:
            os.remove(output)
        except OSError:
            pass
        return None
    finally:
        if image is not None:
            bpy.data.images.remove(image)
        bpy.data.scenes.remove(scene)

    return output if os.path.isfile(output) and os.path.getsize(output) else None


def _wants_still(context):
    from . import properties

    try:
        return properties.get(context).review_as == 'STILL'
    except Exception:
        return False


def _frame_in_hand(context, files):
    """The rendered frame the playhead is on, or the first one.

    A movie is the right answer for a sequence and the wrong one for a look
    - Kitsu shows an image at full size and plays a one-or-two-frame movie
    as a flicker, so publishing a still has to be sayable.
    """
    import re as _re

    scene = getattr(context, 'scene', None)
    current = getattr(scene, 'frame_current', None) if scene else None
    if current is None:
        return files[0]

    for path in files:
        digits = _re.findall(r'(\d+)', os.path.basename(path))
        if digits and int(digits[-1]) == int(current):
            return path
    return files[0]


def prepare(context, last_render):
    '''The file to send to Kitsu, and whether it is temporary.

    A single still or an already-playable movie goes as it is. A sequence of
    frames - which is what a VFX render is - becomes an MP4 first.
    '''
    files = frames_on_disk(last_render)
    if not files:
        return None, False, 'nothing rendered to submit'

    if _wants_still(context) and len(files) > 1:
        # Asked for one image out of a sequence. The frame on the playhead
        # if it was rendered, because that is the one being looked at.
        files = [_frame_in_hand(context, files)]

    if len(files) == 1:
        extension = os.path.splitext(files[0])[1].lstrip(".").lower()
        if extension in PLAYABLE:
            return files[0], False, ''

        # A single frame Kitsu cannot read - an EXR still - becomes a PNG
        # rather than a one-frame movie. Kitsu shows stills perfectly well,
        # and a video of one frame is a worse thing to review.
        still = convert_still(context, files[0])
        if still:
            return still, True, ''
        return None, False, 'could not convert %s for Kitsu' % os.path.basename(files[0])

    movie = build_movie(context, last_render, files)
    if not movie:
        return None, False, 'could not build a movie from the render'
    return movie, True, ''


def submit(context, comment='', task_status_id=None):
    '''Send the last render to Kitsu. Returns a note for the operator.'''
    state = session.state
    last_render = state.last_render

    if not last_render:
        return 'nothing has been rendered this session'
    if not state.connected:
        return 'not connected to Kitsu'

    entity_context = last_render.get('context')
    if entity_context is None or not entity_context.task_id:
        return 'that render has no Kitsu task attached'

    path, temporary, problem = prepare(context, last_render)
    if problem:
        return problem

    client = state.client
    task_id = entity_context.task_id
    text = comment or ('%s rendered from Blender' % last_render.get('stem'))

    def work():
        preferences = prefs.get(context)
        return client.publish_preview(
            task_id, path, comment=text, task_status_id=task_status_id,
            normalize=bool(getattr(preferences, 'kitsu_normalize', False)),
            log=lambda message: print('[BB review] %s' % message))

    def done(_result, error):
        if temporary:
            try:
                os.remove(path)
            except OSError:
                pass
        if error:
            state.say('Kitsu upload failed: %s' % error, error=True)
            return
        state.say('submitted %s to Kitsu' % os.path.basename(path))
        _version_up_after_publish(context)

    session.run('submitting to Kitsu', work, done, background=True)
    return 'uploading %s' % os.path.basename(path)


def _version_up_after_publish(context):
    """Cut the next version once a render has been published.

    So the work file and the Kitsu revision stay in step: publishing three
    renders from one saved version puts three revisions against it and
    nothing on disk tells them apart. Publishing closes a version instead.

    Quietly - the publish has just posted its own comment, and asking to
    publish again is how one piece of work becomes two revisions.
    """
    import bpy

    from . import prefs as _prefs

    preferences = _prefs.get(context)
    if preferences is not None and not preferences.version_up_on_publish:
        return

    def later():
        try:
            bpy.ops.bb.save_next_version(announce=False)
        except Exception as error:
            print('BB Kitsu Pipeline: could not version up after publish (%s)'
                  % error)
        return None

    # From a timer, because this lands in a background job's callback and
    # saving a file is not something to do from under one.
    bpy.app.timers.register(later, first_interval=0.0)


def summary(context=None):
    '''What the review panel should say about the last render.'''
    last_render = session.state.last_render
    if not last_render:
        return []

    files = frames_on_disk(last_render)
    lines = ['%s' % last_render.get('stem', '?')]

    if not files:
        lines.append('no frames found on disk yet')
        return lines

    if len(files) == 1:
        name = os.path.basename(files[0])
        extension = os.path.splitext(name)[1].lstrip('.').lower()
        if extension in PLAYABLE:
            lines.append(name)
        else:
            preferences = prefs.get(context)
            wanted = getattr(preferences, 'still_format', 'PNG') if preferences else 'PNG'
            lines.append('%s - will upload as %s' % (name, wanted))
    else:
        extension = os.path.splitext(files[0])[1].lstrip('.').upper()
        lines.append('%d %s frames - will upload as H.264' % (len(files), extension))
    return lines
