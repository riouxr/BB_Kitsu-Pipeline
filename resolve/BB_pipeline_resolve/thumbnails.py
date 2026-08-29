'''Kitsu thumbnails for the Resolve shot browser.

Same two rules as the Blender and Nuke sides: a preview file is immutable in
Kitsu so one fetched once is never fetched again, and the download never
happens while the UI is being drawn.

Resolve's Fusion UIManager wants a file path to build an ``Icon`` from, not
raw bytes, so - unlike the Nuke side, which hands PySide6 a QPixmap straight
from memory - this always lands the PNG on disk first.
'''
import os
import tempfile

_paths = {}      # preview_file_id -> temp file path
_missing = set()  # preview_file_id known to have no thumbnail


def fetch_path(client, entity):
    '''The temp PNG path for a shot's thumbnail, or "" if it has none.'''
    preview_id = (entity or {}).get('preview_file_id')
    if not preview_id or preview_id in _missing:
        return ''
    if preview_id in _paths:
        return _paths[preview_id]

    try:
        data = client.thumbnail(preview_id) if client else None
    except Exception:
        data = None

    if not data:
        _missing.add(preview_id)
        return ''

    fd, path = tempfile.mkstemp(prefix='bb_kitsu_thumb_', suffix='.png')
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)
    _paths[preview_id] = path
    return path


def apply(ui, widget, client, entity):
    '''Show a shot's thumbnail on ``widget``, or clear it when there is none.

    ``widget`` is a flat, disabled Button - Fusion's UIManager documents
    ``Icon`` as a Button property, unlike a bitmap on a bare Label, which
    this tool tried first and never got to render. Wrapped in a broad
    ``except`` regardless: a thumbnail that fails to render must never take
    the browser down with it.
    '''
    path = fetch_path(client, entity)
    try:
        widget.Icon = ui.Icon({'File': path}) if path else ui.Icon({})
    except Exception as error:
        print('[kitsu] thumbnail not shown: %s' % error)


_bytes_cache = {}  # preview_file_id -> raw PNG bytes, for the PySide (UI-B) path


def fetch_bytes(client, entity):
    '''The thumbnail's raw PNG bytes, or None. Shares the "no thumbnail"
    cache with fetch_path, since either side asking settles it for both.
    '''
    preview_id = (entity or {}).get('preview_file_id')
    if not preview_id or preview_id in _missing:
        return None
    if preview_id in _bytes_cache:
        return _bytes_cache[preview_id]

    try:
        data = client.thumbnail(preview_id) if client else None
    except Exception:
        data = None

    if not data:
        _missing.add(preview_id)
        return None

    _bytes_cache[preview_id] = data
    return data


def qpixmap(client, entity, width=150):
    '''A QPixmap for a shot's thumbnail, or None if it has none.

    The same approach the Nuke side already uses successfully - real Qt, no
    file round trip needed the way Fusion's UIManager (UI-A) required.
    '''
    data = fetch_bytes(client, entity)
    if not data:
        return None
    try:
        try:
            from PySide6 import QtCore, QtGui
        except ImportError:
            from PySide2 import QtCore, QtGui
    except ImportError:
        return None

    image = QtGui.QPixmap()
    if not image.loadFromData(data):
        return None
    if width and image.width() > width:
        image = image.scaledToWidth(width, QtCore.Qt.SmoothTransformation)
    return image


def clear():
    _paths.clear()
    _bytes_cache.clear()
    _missing.clear()
