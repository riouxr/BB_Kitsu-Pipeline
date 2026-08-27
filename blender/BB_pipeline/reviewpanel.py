'''The submit panel in the render window.

Blender opens the Render Result in an Image Editor, and an Image Editor has a
sidebar. That is where a comment and a Submit button belong: the render is on
screen, the artist is already looking at it, and nothing else has to be found
first.

The panel is deliberately quiet when there is nothing to submit. A render
window opened for something outside the pipeline should not grow a tab full of
disabled buttons.
'''
import bpy
from bpy.types import Panel

from . import properties, review, session


class BB_PT_review(Panel):
    bl_label = 'Kitsu Review'
    bl_idname = 'BB_PT_review'
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'Kitsu'

    @classmethod
    def poll(cls, context):
        from . import core
        return core.available

    def draw(self, context):
        layout = self.layout
        state = session.state
        props = properties.get(context)

        lines = review.summary(context)
        if not lines:
            layout.label(text='Nothing rendered yet', icon='RENDER_RESULT')
            layout.label(text='Use the Kitsu menu to render')
            return

        box = layout.box()
        box.label(text=lines[0], icon='RENDER_RESULT')
        for line in lines[1:]:
            row = box.row()
            row.enabled = False
            row.label(text=line)

        last_render = state.last_render or {}
        entity_context = last_render.get('context')

        if not state.connected:
            row = layout.row()
            row.alert = True
            row.label(text='Not connected to Kitsu', icon='UNLINKED')
            layout.operator('bb.connect', text='Connect...', icon='LINKED')
            return

        if entity_context is None or not entity_context.task_id:
            row = layout.row()
            row.alert = True
            row.label(text='No Kitsu task on that render', icon='ERROR')
            return

        column = layout.column()
        column.use_property_split = False
        column.prop(props, 'comment')

        # Only when there is a choice to make. One frame is an image either
        # way, and offering to turn it into a movie would be offering a
        # worse result.
        if _rendered_frames(context) > 1:
            row = column.row()
            row.use_property_split = False
            row.prop(props, 'review_as', expand=True)

        column.prop(props, 'task_status')

        row = layout.row()
        row.enabled = not state.busy
        row.scale_y = 1.4
        row.operator('bb.submit_render', icon='EXPORT')

        if state.busy:
            note = layout.row()
            note.enabled = False
            note.label(text='%s...' % state.busy, icon='SORTTIME')

        if state.message:
            note = layout.row()
            note.alert = state.is_error
            note.label(text=state.message,
                       icon='ERROR' if state.is_error else 'INFO')


def _rendered_frames(context):
    """How many frames the last render produced, or 0."""
    from . import review

    try:
        return len(review.frames_on_disk(session.state.last_render))
    except Exception:
        return 0


classes = (BB_PT_review,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
