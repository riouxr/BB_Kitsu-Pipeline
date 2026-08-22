'''Kitsu thumbnails for the Nuke browser.

The same idea as the Blender side and the same two rules: a preview file is
immutable in Kitsu so one fetched once is never fetched again, and the
download never happens while the UI is being drawn.

Qt is imported inside the functions so this module can be exercised by a plain
interpreter with no PySide6 - which is how it gets tested on a machine that
cannot run Nuke.
'''
# preview_file_id -> raw PNG bytes. Small, and there are only ever a handful.
_cache = {}
_missing = set()


def fetch(client, entity):
    '''Download and cache a shot's thumbnail. Returns the bytes, or None.'''
    preview_id = (entity or {}).get('preview_file_id')
    if not preview_id or preview_id in _missing:
        return None
    if preview_id in _cache:
        return _cache[preview_id]

    try:
        data = client.thumbnail(preview_id)
    except Exception:
        data = None

    if not data:
        _missing.add(preview_id)
        return None

    _cache[preview_id] = data
    return data


def pixmap(client, entity, width=220):
    '''A QPixmap for a shot, or None when Kitsu has no thumbnail for it.'''
    data = fetch(client, entity)
    if not data:
        return None

    try:
        from PySide6 import QtCore, QtGui
    except ImportError:
        return None

    image = QtGui.QPixmap()
    if not image.loadFromData(data):
        return None

    if width and image.width() > width:
        image = image.scaledToWidth(width, QtCore.Qt.SmoothTransformation)
    return image


def clear():
    _cache.clear()
    _missing.clear()
