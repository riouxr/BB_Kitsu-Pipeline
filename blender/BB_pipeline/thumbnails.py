'''Kitsu thumbnails as Blender UI icons.

Built on ``bpy.utils.previews`` rather than ``bpy.data.images``. An image
loaded into bpy.data belongs to the blend file - it shows up in the image
list and would be one more thing to clean out of every saved work file -
whereas a preview collection is exactly the mechanism Blender provides for
add-on icons and is thrown away on unregister.

Downloads never happen in a draw callback. Draw runs on every redraw, and a
network request there would stall the interface; the browser fetches when the
selection changes and the panel only reads what is already cached.
'''
import os
import tempfile

import bpy.utils.previews

# preview_file_ids already loaded into the collection. A preview file is
# immutable in Kitsu, so one that has been fetched once never needs fetching
# again.
#
# The icon *id* is deliberately not cached. Blender allocates it lazily, so it
# can still be 0 just after loading - caching that zero would leave the icon
# permanently blank. The collection is asked for it at draw time instead.
_loaded = set()
_collection = None
_files = []

# Remembered so a shot with no thumbnail is not asked for on every selection.
_missing = set()


def _ensure_collection():
    global _collection
    if _collection is None:
        _collection = bpy.utils.previews.new()
    return _collection


def icon_id(entity):
    '''The icon for a shot or asset, or 0 when there is not one.'''
    preview_id = (entity or {}).get('preview_file_id')
    if not preview_id or preview_id not in _loaded or _collection is None:
        return 0
    try:
        return _collection[preview_id].icon_id
    except KeyError:
        return 0


def fetch(client, entity):
    '''Download and cache the thumbnail for one entity. Returns the icon id.

    Safe to call repeatedly - a cached id, or a known-missing one, costs
    nothing.
    '''
    preview_id = (entity or {}).get('preview_file_id')
    if not preview_id or preview_id in _missing:
        return 0
    if preview_id in _loaded:
        return icon_id(entity)

    data = None
    try:
        data = client.thumbnail(preview_id)
    except Exception:
        data = None

    if not data:
        _missing.add(preview_id)
        return 0

    handle, path = tempfile.mkstemp(prefix='bb_thumb_', suffix='.png')
    with os.fdopen(handle, 'wb') as out:
        out.write(data)
    _files.append(path)

    try:
        _ensure_collection().load(preview_id, path, 'IMAGE')
        _loaded.add(preview_id)
    except Exception:
        _missing.add(preview_id)
        return 0

    return icon_id(entity)


def draw(layout, entity, scale=6.0):
    '''Draw the entity's thumbnail, or nothing when it has none.'''
    icon = icon_id(entity)
    if not icon:
        return False
    row = layout.row()
    row.alignment = 'CENTER'
    row.template_icon(icon_value=icon, scale=scale)
    return True


def clear():
    global _collection
    if _collection is not None:
        bpy.utils.previews.remove(_collection)
        _collection = None
    _loaded.clear()
    _missing.clear()

    for path in _files:
        try:
            os.remove(path)
        except OSError:
            pass
    del _files[:]


def register():
    _ensure_collection()


def unregister():
    clear()
