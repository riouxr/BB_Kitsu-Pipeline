'''The Kitsu entry in Blender's main menu bar.

Blender builds the top row from ``TOPBAR_MT_editor_menus.draw``, and anything
appended to that class draws *after* Help. Getting an entry between Window and
Help means replacing that draw method, so this keeps the original, installs a
copy with one extra line, and puts the original back on unregister.

Replacing a method Blender owns is only safe while it looks the way we expect,
so the original's source is checked first. If a future release reorders or
renames those menus the patch is skipped and the entry is appended instead -
in the wrong place, but present and working, rather than silently eating
whatever the new version added.
'''
import inspect

import bpy
from bpy.types import Menu

from . import prefs, scenesync, session, stamp

# The menus the stock draw emits, in order. The patch only applies if all of
# them are still there.
EXPECTED = ('TOPBAR_MT_blender', 'TOPBAR_MT_file', 'TOPBAR_MT_edit',
            'TOPBAR_MT_render', 'TOPBAR_MT_window', 'TOPBAR_MT_help')

_original_draw = None
_appended = None


class BB_MT_main(Menu):
    bl_idname = 'BB_MT_main'
    bl_label = 'Kitsu'

    def draw(self, context):
        from . import core

        layout = self.layout

        if not core.available:
            layout.label(text=core.error, icon='ERROR')
            return

        state = session.state

        shot_context, source = stamp.read_current()
        if shot_context is not None and shot_context.is_complete():
            layout.label(text=shot_context.versioned(), icon='FILE_BLEND')
            if source == 'filename':
                layout.label(text='context from filename - no Kitsu ids',
                             icon='INFO')
        else:
            layout.label(text='Unsaved / outside the pipeline', icon='FILE_BLANK')

        layout.separator()

        ready, why = prefs.roots_ready(context)
        if not ready:
            row = layout.row()
            row.alert = True
            row.label(text=why, icon='ERROR')

        row = layout.row()
        row.enabled = state.connected
        row.operator('bb.browser', text='Browser...', icon='FILEBROWSER')

        differences = scenesync.pending(context)
        if differences:
            row = layout.row()
            row.alert = True
            row.label(text='Kitsu: %s' % ', '.join(differences), icon='TIME')
            layout.operator('bb.apply_frame_range', icon='CHECKMARK')

        layout.separator()
        layout.operator('bb.render_image', icon='RENDER_STILL')
        layout.operator('bb.render_animation', icon='RENDER_ANIMATION')
        layout.operator('bb.render_playblast', icon='SEQUENCE')

        layout.separator()
        layout.operator('bb.save_next_version', icon='DUPLICATE')
        layout.operator('bb.publish_save', text='Update Kitsu...',
                        icon='EXPORT')
        layout.operator('bb.open_work_folder', icon='FILE_FOLDER')

        layout.separator()

        # The add-on signs in by itself at startup, so Connect is only worth
        # showing when that could not happen - no stored password, or the
        # server was unreachable. There is deliberately no Disconnect: the
        # session costs nothing and dropping it only means signing back in.
        if not state.connected:
            layout.operator('bb.connect', text='Connect to Kitsu...',
                            icon='LINKED')

        layout.operator('bb.open_preferences', icon='PREFERENCES')

        if state.message:
            layout.separator()
            layout.label(text=state.message,
                         icon='ERROR' if state.is_error else 'INFO')


def _patched_draw(self, context):
    '''The stock top bar, with Kitsu inserted before Help.'''
    layout = self.layout

    if getattr(context.area, 'show_menus', False):
        layout.menu('TOPBAR_MT_blender', text='', icon='BLENDER')
    else:
        layout.menu('TOPBAR_MT_blender', text='Blender')

    layout.menu('TOPBAR_MT_file')
    layout.menu('TOPBAR_MT_edit')
    layout.menu('TOPBAR_MT_render')
    layout.menu('TOPBAR_MT_window')
    layout.menu('BB_MT_main')
    layout.menu('TOPBAR_MT_help')


def _append_draw(self, context):
    self.layout.menu('BB_MT_main')


def _stock_draw_is_recognised():
    try:
        source = inspect.getsource(bpy.types.TOPBAR_MT_editor_menus.draw)
    except (OSError, TypeError):
        return False
    return all(name in source for name in EXPECTED)


def register():
    global _original_draw, _appended

    bpy.utils.register_class(BB_MT_main)

    if _stock_draw_is_recognised():
        _original_draw = bpy.types.TOPBAR_MT_editor_menus.draw
        bpy.types.TOPBAR_MT_editor_menus.draw = _patched_draw
    else:
        print('BB Kitsu Pipeline: unfamiliar top bar, appending the menu after Help')
        _appended = _append_draw
        bpy.types.TOPBAR_MT_editor_menus.append(_appended)


def unregister():
    global _original_draw, _appended

    if _original_draw is not None:
        bpy.types.TOPBAR_MT_editor_menus.draw = _original_draw
        _original_draw = None
    if _appended is not None:
        bpy.types.TOPBAR_MT_editor_menus.remove(_appended)
        _appended = None

    bpy.utils.unregister_class(BB_MT_main)
