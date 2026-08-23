'''Browser operators.

The file operations all go through the same two rules:

  * a version number is produced once, by the core, from what is on disk -
    never typed in and never inferred twice;
  * whatever is saved is stamped with the context that named it, so the file
    and its Kitsu identity cannot drift apart.

The browser itself is a dialog rather than a panel. A dialog can only carry
property widgets - clicking anything that runs an operator dismisses it - so
it is built as five selectors plus a choice of what to do, and the single OK
button performs it.
'''
import os
import subprocess
import sys
import traceback
from pathlib import Path

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator

from . import (capture, fetch, handlers, prefs, properties, publish,
               render, review, scenesync, session, stamp, thumbnails,
               treeview)


def _later(function):
    """Run something once the popup that asked for it has closed.

    Anything that loads or replaces the file - open_mainfile, read_homefile,
    the append and link browsers - frees the data the popup is drawn from. Run
    straight from a popup button that is a use-after-free, and what comes back
    is an empty scene. A zero-delay timer fires on the next tick, by which
    point the popup is gone.

    A dialog with an OK button did not need this, because its execute() only
    ran after the dialog closed; a popup whose buttons act immediately does.
    """
    if bpy.app.background:
        # No popup to outlive, and no timer loop to fire one either.
        function()
        return

    def once():
        try:
            function()
        except Exception:
            traceback.print_exc()
        return None

    bpy.app.timers.register(once, first_interval=0.0)


def _create_version(shot_context, path, from_current):
    """Make the new version, once the popup has gone.

    Split out of the operator because reading the startup file replaces the
    WindowManager - and the popup with it - so none of this can run while the
    button that asked for it is still on screen.
    """
    context = bpy.context

    if not from_current:
        bpy.ops.wm.read_homefile(use_empty=False)

    stamp.write(bpy.context.scene, shot_context)
    framing = scenesync.on_create(context, shot_context)
    _save_with_previews(context, path, shot_context)

    session.state.context = shot_context
    handlers.restore_selection(shot_context)
    fetch.refresh_workfiles()
    session.state.say('created %s%s'
                      % (path.name, ' - %s' % framing if framing else ''))

    _ask_to_publish(bpy.context, path)


def _save_with_previews(context, path, entity_context=None):
    """Save, with the icons the Append and Link browsers will want.

    Datablock previews are generated before the save so they end up inside the
    file, and the file's own thumbnail is embedded by the save itself.
    """
    preferences = prefs.get(context)
    if preferences is None or preferences.generate_previews:
        capture.generate_datablock_previews(context)

    with capture.embedded_preview():
        bpy.ops.wm.save_as_mainfile(filepath=str(path))

    _store_thumb(context, entity_context)


def _store_thumb(context, entity_context):
    """Write the picture the browser shows for this version.

    Blender embeds a thumbnail in the .blend, but nothing can read another
    file's embedded preview without opening it, so the browser needs its own
    copy beside the file. Kitsu cannot supply one either: its preview files
    are numbered by a revision counter that counts publishes and review
    comments, so none of them maps back to v007.
    """
    if entity_context is None:
        return

    from BB_core import workfiles

    picture = capture.viewport_png(context)
    if not picture:
        print('BB Kitsu Pipeline: no viewport to grab a thumbnail from')
        return

    # Reported rather than swallowed. A thumbnail that silently fails to
    # write is indistinguishable from a version that simply has not been
    # saved yet, which is exactly how a wrong config call went unnoticed.
    try:
        stored = workfiles.save_thumb(entity_context, 'blender', picture,
                                      entity_context.version,
                                      prefs.config(context))
        if stored is None:
            print('BB Kitsu Pipeline: could not store the version thumbnail')
    except Exception as error:
        print('BB Kitsu Pipeline: version thumbnail failed (%s)' % error)
    finally:
        capture.discard(picture)


def _ask_to_publish(context, path):
    """Open the publish dialog for a version that was just saved.

    INVOKE_DEFAULT so the dialog opens once the calling operator has finished;
    a dialog cannot be raised from inside another operator's execute.

    A dialog needs a window to sit in, and this can now be reached from a
    timer callback where there may not be one. Without a window the publish
    is skipped rather than raised - the file is saved either way, and
    Kitsu > Update Kitsu still sends it.
    """
    if publish.why_not(context, stamp.read_current()[0]):
        return
    if bpy.app.background or getattr(bpy.context, 'window', None) is None:
        return
    try:
        bpy.ops.bb.publish_save('INVOKE_DEFAULT', filepath=str(path))
    except RuntimeError as error:
        print('BB Kitsu Pipeline: could not open the publish dialog (%s)' % error)


def _core_ready(operator):
    from . import core
    if not core.available:
        operator.report({'ERROR'}, core.error)
        return False
    return True


class BB_OT_connect(Operator):
    bl_idname = 'bb.connect'
    bl_label = 'Connect to Kitsu'
    bl_description = 'Log in to Kitsu and load the project list'

    def invoke(self, context, event):
        if not _core_ready(self):
            return {'CANCELLED'}

        preferences = prefs.get(context)
        if not preferences.server or not preferences.email:
            self.report({'ERROR'},
                        'Set the Kitsu server and your email in the add-on '
                        'preferences first')
            return {'CANCELLED'}

        # A stored password means there is nothing to ask for.
        props = properties.get(context)
        props.password = session.credentials_module.get_password(preferences.email) or ''
        if props.password:
            return self.execute(context)

        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        preferences = prefs.get(context)
        layout = self.layout
        layout.label(text=preferences.server, icon='URL')
        layout.label(text=preferences.email, icon='USER')
        layout.prop(properties.get(context), 'password')

    def execute(self, context):
        props = properties.get(context)
        password = props.password
        props.password = ''

        if not password:
            self.report({'ERROR'}, 'No password given')
            return {'CANCELLED'}

        fetch.connect(context, password)

        state = session.state
        if not state.connected:
            self.report({'ERROR'}, state.message or 'Could not connect')
            return {'CANCELLED'}

        self.report({'INFO'}, state.message)
        return {'FINISHED'}


class BB_OT_disconnect(Operator):
    bl_idname = 'bb.disconnect'
    bl_label = 'Disconnect'
    bl_description = 'Drop the Kitsu session and clear the cached shot lists'

    def execute(self, context):
        fetch.disconnect(context)
        return {'FINISHED'}


class BB_OT_open_preferences(Operator):
    bl_idname = 'bb.open_preferences'
    bl_label = 'Preferences...'
    bl_description = 'Open the BB Kitsu Pipeline add-on preferences'

    def execute(self, context):
        bpy.ops.screen.userpref_show('INVOKE_DEFAULT')
        context.preferences.active_section = 'ADDONS'
        context.window_manager.addon_search = 'BB Kitsu Pipeline'
        return {'FINISHED'}


class BB_OT_forget_password(Operator):
    bl_idname = 'bb.forget_password'
    bl_label = 'Forget Stored Password'
    bl_description = 'Remove the saved password from the Windows Credential Manager'

    def execute(self, context):
        if not _core_ready(self):
            return {'CANCELLED'}

        preferences = prefs.get(context)
        removed = session.credentials_module.delete_password(preferences.email)
        self.report({'INFO'}, 'Password forgotten' if removed else 'Nothing was stored')
        return {'FINISHED'}


# Popup width in Blender's own units. template_icon measures its scale in UI
# units of ~20px, so the two have to agree - and both scale with the
# interface, so the ratio holds whatever the user's UI scale is.
#
# Half the box width, not all of it. Kitsu serves a small thumbnail, and
# blowing it up to the full 460 only magnifies the pixels.
BROWSER_WIDTH = 760
BOX_PADDING = 24
PREVIEW_SCALE = (BROWSER_WIDTH - BOX_PADDING) / 20.0 / 2.0

# template_icon measures its scale in UI units of ~20px, so this is a row a
# little over two lines tall - big enough to recognise a frame, small enough
# that ten versions still fit without scrolling.
VERSION_ICON_SCALE = 2.4


class BB_OT_browser(Operator):
    bl_idname = 'bb.browser'
    bl_label = 'Kitsu Browser'
    bl_description = ('Pick an asset or shot and a task, then open or create '
                      'a scene file')

    @classmethod
    def poll(cls, context):
        return session.state.connected

    def invoke(self, context, event):
        if not _core_ready(self):
            return {'CANCELLED'}
        # Kitsu and the disk both move while Blender is open, so both are
        # re-read every time the browser is opened rather than cached from
        # whenever the project was first picked.
        fetch.refresh_project(context)
        fetch.refresh_workfiles(context)
        # The bookmark restores the selection, but the tree opens collapsed,
        # so without this the remembered shot is sitting inside a branch the
        # artist cannot see - which looks exactly like nothing was
        # remembered at all.
        treeview.reveal(properties.get(context))
        # A popup rather than a dialog: the buttons in it do the work
        # themselves, so there is nothing for an OK to confirm, and clicking
        # away in the viewport dismisses it.
        return context.window_manager.invoke_popup(self, width=BROWSER_WIDTH)

    def draw(self, context):
        props = properties.get(context)
        state = session.state

        layout = self.layout
        layout.use_property_split = False
        layout.use_property_decorate = False

        # One header line: which show, and which of its two trees.
        header = layout.row(align=True)
        header.prop(props, 'project', text='')
        tabs = header.row(align=True)
        tabs.prop(props, 'entity_type', expand=True)
        # Shown because there is otherwise no way to tell which build Blender
        # actually loaded - a junctioned add-on and a stale copy look
        # identical from the menu.
        stamp_row = header.row()
        stamp_row.enabled = False
        stamp_row.label(text=core.version())

        split = layout.split(factor=0.38)

        # -- left: the tree ---------------------------------------------------
        left = split.column()
        tree_box = left.box()
        treeview.draw(tree_box, props)
        self._draw_entity_facts(left, props)

        # -- right: the versions ----------------------------------------------
        right = split.column()
        entity_context = fetch.current_context(context)

        if entity_context is None:
            note = right.box()
            note.label(text='Pick a task on the left', icon='INFO')
            return

        ready, why = prefs.roots_ready(context)
        if not ready:
            note = right.box()
            note.alert = True
            note.label(text=why, icon='ERROR')
            note.label(text='Kitsu > Preferences...')
            return

        self._draw_versions(right, context, props, entity_context)

        if state.message:
            row = layout.row()
            row.alert = state.is_error
            row.label(text=state.message,
                      icon='ERROR' if state.is_error else 'INFO')

    def _draw_entity_facts(self, layout, props):
        '''Range, rate and size for the selection - one line, not a panel.

        Prism gives this a whole box with a thumbnail slot in it, which on a
        shot with no data is a large area of nothing. It is three numbers.
        '''
        from BB_core import frames

        state = session.state
        entity = state.entity(props.entity_id, props.is_asset)
        if entity is None:
            return

        project = state.project(props.project) or {}
        facts = []

        if not props.is_asset:
            first, last = frames.frame_range(entity)
            if first is not None and last is not None:
                facts.append('%d-%d' % (first, last))

        rate = frames.fps(project, entity)
        if rate:
            facts.append('%s fps' % frames.describe(rate))

        size = frames.resolution(project, entity)
        if size:
            facts.append('%dx%d' % size)

        if not facts:
            return

        row = layout.row()
        row.enabled = False
        row.label(text=' · '.join(facts))

    def _draw_versions(self, layout, context, props, entity_context):
        '''One row per version on disk, each with the picture it saved.'''
        from BB_core import workfiles

        state = session.state
        has_files = bool(state.workfiles)

        listing = layout.box()
        if not has_files:
            empty = listing.row()
            empty.enabled = False
            empty.label(text='no versions yet', icon='FILE_BLANK')
        else:
            config = prefs.config(context)
            # What Kitsu knows about this shot or asset, used for any version
            # that has no picture of its own.
            fallback = thumbnails.icon_id(
                state.entity(props.entity_id, props.is_asset))
            column = listing.column(align=True)
            for version, path in reversed(state.workfiles):
                self._draw_version_row(column, props, entity_context, version,
                                       path, config, workfiles, fallback)

        next_version = (state.workfiles[-1][0] + 1) if has_files else 1
        made = ('Create v%03d' % next_version if not has_files
                else 'New v%03d' % next_version)

        buttons = layout.column(align=True)
        open_row = buttons.row(align=True)
        open_row.enabled = has_files
        open_row.operator('bb.open_workfile', text='Open', icon='FILE_FOLDER')

        buttons.operator('bb.new_workfile', text=made,
                         icon='FILE_NEW').from_current = False
        buttons.operator('bb.new_workfile', text='%s from Current Scene' % made,
                         icon='DUPLICATE').from_current = True

        buttons.separator()

        # Append and Link hand off to Blender's own browser, the only one that
        # can descend into a .blend.
        linking = buttons.row(align=True)
        linking.enabled = has_files
        linking.operator('bb.append_workfile', text='Append', icon='APPEND_BLEND')
        linking.operator('bb.link_workfile', text='Link', icon='LINK_BLEND')

    def _draw_version_row(self, column, props, entity_context, version, path,
                          config, workfiles, fallback=0):
        chosen = props.version == str(version)

        row = column.row(align=True)
        row.emboss = 'NORMAL' if chosen else 'NONE'

        try:
            thumb = workfiles.thumb_file(entity_context, 'blender', version,
                                         config)
        except Exception:
            thumb = None

        # The version's own picture when it has one, otherwise the entity
        # thumbnail from Kitsu. Kitsu cannot say what a particular version
        # looked like - its preview files are numbered by a revision counter
        # that counts publishes and review comments - but it can say what the
        # shot is, which is what makes a row recognisable at a glance.
        icon_id = (thumbnails.version_icon(thumb) if thumb else 0) or fallback
        if icon_id:
            row.template_icon(icon_value=icon_id, scale=VERSION_ICON_SCALE)
        else:
            spacer = row.row()
            spacer.enabled = False
            spacer.label(text='', icon='IMAGE_DATA')

        button = row.operator('bb.tree_pick', text='v%03d' % version,
                              depress=chosen)
        button.kind = 'version'
        button.identifier = str(version)

    def execute(self, context):
        # Nothing to do on close: the popup's own buttons have already run
        # whatever was asked for.
        return {'FINISHED'}


class BB_OT_publish_save(Operator):
    bl_idname = 'bb.publish_save'
    bl_label = 'Update Kitsu'
    bl_description = 'Post a comment, a status and a viewport preview for this version'
    bl_options = {'REGISTER', 'INTERNAL'}

    filepath: StringProperty(options={'HIDDEN'})

    def _context(self):
        return stamp.read_current()[0]

    def invoke(self, context, event):
        # Called straight from the menu there is no path to pass in, so fall
        # back to whatever is open.
        if not self.filepath:
            self.filepath = bpy.data.filepath
        if not self.filepath:
            self.report({'ERROR'}, 'Save the file first')
            return {'CANCELLED'}

        entity_context = self._context()

        blocked = publish.why_not(context, entity_context)
        if blocked:
            # Nothing to publish to; say so and do not ask for a comment that
            # has nowhere to go.
            self.report({'INFO'}, blocked)
            return {'CANCELLED'}

        props = properties.get(context)
        props.comment = publish.default_comment(Path(self.filepath))
        properties.clear(props, 'task_status')

        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        props = properties.get(context)
        entity_context = self._context()

        layout = self.layout
        if entity_context is not None and entity_context.is_complete():
            layout.label(text=entity_context.versioned(), icon='FILE_BLEND')

        column = layout.column()
        column.use_property_split = True
        column.prop(props, 'comment')
        column.prop(props, 'task_status')

        preferences = prefs.get(context)
        row = column.row()
        row.prop(preferences, 'preview_on_save')

    def execute(self, context):
        props = properties.get(context)
        entity_context = self._context()

        note = publish.send(context, entity_context, Path(self.filepath),
                            comment=props.comment,
                            task_status_id=properties.selected_status_id(context))
        self.report({'INFO'}, note or 'nothing sent to Kitsu')
        return {'FINISHED'}


class BB_OT_open_workfile(Operator):
    bl_idname = 'bb.open_workfile'
    bl_label = 'Open'
    bl_description = 'Open the selected scene file'
    bl_options = {'REGISTER', 'INTERNAL'}

    filepath: StringProperty(subtype='FILE_PATH', options={'HIDDEN'})

    def execute(self, context):
        if not self.filepath:
            entry = properties.selected_version(context)
            self.filepath = str(entry[1]) if entry else ''

        if not self.filepath or not os.path.isfile(self.filepath):
            self.report({'ERROR'}, 'File is gone: %s' % self.filepath)
            fetch.refresh_workfiles(context)
            return {'CANCELLED'}

        path = self.filepath
        _later(lambda: bpy.ops.wm.open_mainfile(filepath=path))
        return {'FINISHED'}


class _LibraryLoad:
    """Shared behaviour for Append and Link.

    Both hand straight off to Blender's own wm.append / wm.link, because that
    is the only file browser that can descend into a .blend and list the
    objects, collections and scenes inside it. Pointing `directory` at
    "<file>.blend/" is what opens it already inside the file.

    display_type is forced to THUMBNAIL so previews show rather than a list of
    names - which is the whole point of stamping a preview into the file when
    it is saved.
    """

    bl_options = {'REGISTER', 'INTERNAL'}
    use_link = False

    @classmethod
    def poll(cls, context):
        return bool(session.state.workfiles)

    def execute(self, context):
        entry = properties.selected_version(context)
        if entry is None:
            self.report({'ERROR'}, 'No scene file selected')
            return {'CANCELLED'}

        path = entry[1]
        if not path.is_file():
            self.report({'ERROR'}, 'File is gone: %s' % path)
            fetch.refresh_workfiles(context)
            return {'CANCELLED'}

        # The trailing separator is what tells Blender to open inside the
        # .blend rather than in the folder that holds it.
        inside = str(path) + os.sep
        operator = bpy.ops.wm.link if self.use_link else bpy.ops.wm.append
        _later(lambda: operator('INVOKE_DEFAULT',
                                filepath=inside, directory=inside, filename='',
                                display_type='THUMBNAIL'))
        return {'FINISHED'}


class BB_OT_append_workfile(_LibraryLoad, Operator):
    bl_idname = 'bb.append_workfile'
    bl_label = 'Append'
    bl_description = ('Append from the selected version, using the Blender '
                      'file browser opened inside the .blend')
    use_link = False


class BB_OT_link_workfile(_LibraryLoad, Operator):
    bl_idname = 'bb.link_workfile'
    bl_label = 'Link'
    bl_description = ('Link from the selected version, using the Blender '
                      'file browser opened inside the .blend')
    use_link = True


class BB_OT_new_workfile(Operator):
    bl_idname = 'bb.new_workfile'
    bl_label = 'New Version'
    bl_description = ('Create the next scene file version for this task, '
                      'starting at v001 when there is nothing yet')
    bl_options = {'REGISTER', 'INTERNAL'}

    from_current: BoolProperty(
        name='From Current Scene',
        description='Start from the open scene instead of the startup file',
        default=False,
    )

    def execute(self, context):
        if not _core_ready(self):
            return {'CANCELLED'}

        # The browser hides the buttons when this is not set, but the operator
        # is also reachable from the menu and from search.
        ready, why = prefs.roots_ready(context)
        if not ready:
            self.report({'ERROR'}, why)
            return {'CANCELLED'}

        shot_context = fetch.current_context(context)
        if shot_context is None:
            self.report({'ERROR'}, 'Pick a project, sequence, shot and task first')
            return {'CANCELLED'}

        try:
            config = prefs.config(context)
            path, version = session.workfiles_module.next_workfile(
                shot_context, 'blender', config)
        except session.workfiles_module.RootNotConfigured as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

        os.makedirs(path.parent, exist_ok=True)

        # The version came from the core a moment ago; the context that gets
        # stamped is the same one that produced the filename, so the two can
        # never disagree.
        shot_context = shot_context.at_version(version)

        # Reading the startup file replaces everything, including the popup
        # this was clicked in, so the work waits until that popup is gone.
        _later(lambda: _create_version(shot_context, path, self.from_current))

        self.report({'INFO'}, 'Creating %s' % path.name)
        return {'FINISHED'}


class BB_OT_save_next_version(Operator):
    bl_idname = 'bb.save_next_version'
    bl_label = 'Save Next Version'
    bl_description = 'Save the open scene as the next version of itself'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath)

    def execute(self, context):
        if not _core_ready(self):
            return {'CANCELLED'}

        try:
            config = prefs.config(context)
        except Exception:
            config = None

        try:
            path, version = session.versioning_module.bump(bpy.data.filepath, config)
        except ValueError:
            self.report(
                {'ERROR'},
                'This file is not named to the pipeline scheme - use the Shot '
                'Browser to start one that is')
            return {'CANCELLED'}

        shot_context, source = stamp.read_current()
        if shot_context is None:
            self.report({'ERROR'}, 'No pipeline context on this scene')
            return {'CANCELLED'}

        shot_context = shot_context.at_version(version)
        stamp.write(context.scene, shot_context)
        _save_with_previews(context, path, shot_context)

        session.state.context = shot_context
        fetch.refresh_workfiles()

        if source == 'filename':
            self.report({'WARNING'},
                        'Saved %s - context recovered from the filename, so it '
                        'carries no Kitsu ids yet' % path.name)
        else:
            self.report({'INFO'}, 'Saved %s' % path.name)
        _ask_to_publish(context, path)
        return {'FINISHED'}


class BB_OT_apply_frame_range(Operator):
    bl_idname = 'bb.apply_frame_range'
    bl_label = 'Apply Kitsu Frame Range'
    bl_description = ('Set this scene to the frame range and frame rate Kitsu '
                      'has for the shot')

    @classmethod
    def poll(cls, context):
        return bool(scenesync.pending(context))

    def execute(self, context):
        entity_context = stamp.read_current()[0]
        settings = scenesync.kitsu_settings(entity_context, refresh=True)
        changed = scenesync.apply(context.scene, settings) if settings else []

        if not changed:
            self.report({'INFO'}, 'Already matches Kitsu')
            return {'CANCELLED'}

        self.report({'INFO'}, 'Applied %s' % ', '.join(changed))
        return {'FINISHED'}


class _Render:
    """Shared behaviour for the three render entries."""

    bl_options = {'REGISTER'}
    kind = render.IMAGE

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath)

    def execute(self, context):
        if not _core_ready(self):
            return {'CANCELLED'}
        try:
            note = render.run(context, self.kind)
        except render.RenderSetup as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

        self.report({'INFO'}, note)
        return {'FINISHED'}


class BB_OT_render_image(_Render, Operator):
    bl_idname = 'bb.render_image'
    bl_label = 'Render Image'
    bl_description = 'Render the current frame to the version render folder'
    kind = render.IMAGE


class BB_OT_render_animation(_Render, Operator):
    bl_idname = 'bb.render_animation'
    bl_label = 'Render Animation'
    bl_description = 'Render the frame range to the version render folder'
    kind = render.ANIMATION


class BB_OT_render_playblast(_Render, Operator):
    bl_idname = 'bb.render_playblast'
    bl_label = 'Render Playblast'
    bl_description = ('OpenGL pass of the frame range, straight to H.264 for '
                      'review')
    kind = render.PLAYBLAST


class BB_OT_submit_render(Operator):
    bl_idname = 'bb.submit_render'
    bl_label = 'Submit to Kitsu'
    bl_description = ('Upload the last render as a comment on its Kitsu task, '
                      'converting the frames to H.264 first')
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(session.state.last_render) and session.state.connected

    def execute(self, context):
        if not _core_ready(self):
            return {'CANCELLED'}

        props = properties.get(context)
        note = review.submit(context, comment=props.comment,
                             task_status_id=properties.selected_status_id(context))

        if note.startswith('uploading'):
            props.comment = ''
            self.report({'INFO'}, note)
            return {'FINISHED'}

        self.report({'WARNING'}, note)
        return {'CANCELLED'}


class BB_OT_open_work_folder(Operator):
    bl_idname = 'bb.open_work_folder'
    bl_label = 'Open Work Folder'
    bl_description = 'Show the work folder for this task in the file browser'

    def execute(self, context):
        if not _core_ready(self):
            return {'CANCELLED'}

        shot_context = fetch.current_context(context)
        if shot_context is None:
            shot_context = stamp.read_current()[0]
        if shot_context is None or not shot_context.is_complete():
            self.report({'ERROR'}, 'No shot context - pick a task in the browser')
            return {'CANCELLED'}

        try:
            folder = session.workfiles_module.work_dir(shot_context, prefs.config(context))
        except session.workfiles_module.RootNotConfigured as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

        if not folder.is_dir():
            self.report({'WARNING'}, 'Not created yet: %s' % folder)
            return {'CANCELLED'}

        if sys.platform == 'win32':
            os.startfile(str(folder))
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(folder)])
        else:
            subprocess.Popen(['xdg-open', str(folder)])
        return {'FINISHED'}


classes = (
    BB_OT_connect,
    BB_OT_disconnect,
    BB_OT_open_preferences,
    BB_OT_forget_password,
    BB_OT_browser,
    BB_OT_publish_save,
    BB_OT_open_workfile,
    BB_OT_append_workfile,
    BB_OT_link_workfile,
    BB_OT_new_workfile,
    BB_OT_save_next_version,
    BB_OT_apply_frame_range,
    BB_OT_render_image,
    BB_OT_render_animation,
    BB_OT_render_playblast,
    BB_OT_submit_render,
    BB_OT_open_work_folder,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
