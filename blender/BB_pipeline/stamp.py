'''Storing the pipeline context inside the .blend.

The Resolve tool recovers its Kitsu context by parsing it back out of the
project name. That works, but it only survives what the naming scheme can
encode - which is names, never the Kitsu ids needed to publish.

A scene here carries the whole context as a custom property, so reopening a
file restores project/sequence/shot/task *and* their ids without a single
round trip. Filename parsing stays as the fallback for files that predate the
add-on, and it is explicitly marked as id-less so the publish path knows it
still has to resolve them.
'''
import bpy

from . import session


def key():
    from BB_core.context import SCENE_KEY
    return SCENE_KEY


def write(scene, shot_context):
    '''Stamp a context onto a scene.'''
    scene[key()] = shot_context.to_dict()


def read(scene):
    '''The stamped context, or None if this scene has never been stamped.'''
    data = scene.get(key())
    if not data:
        return None

    # Blender hands back an IDPropertyGroup, not a dict.
    try:
        data = data.to_dict()
    except AttributeError:
        data = dict(data)

    return session.ShotContext.from_dict(data)


def read_current():
    '''The context of the scene that is open, stamp first, filename second.'''
    scene = bpy.context.scene
    if scene is not None:
        stamped = read(scene)
        if stamped is not None:
            return stamped, 'stamp'

    path = bpy.data.filepath
    if path:
        from pathlib import Path

        from . import prefs

        # Parse against the configured templates, not the defaults: a studio
        # running four-digit versions would otherwise fail to recognise its
        # own filenames.
        try:
            config = prefs.config()
        except Exception:
            config = None

        recovered = session.ShotContext.from_filename(Path(path).name, config)
        if recovered is not None:
            return recovered, 'filename'

    return None, ''
