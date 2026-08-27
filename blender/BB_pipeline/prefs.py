'''Add-on preferences.

Holds the things that are true for this machine regardless of which shot is
open: the Kitsu server, who you log in as, and the two roots the path
templates hang off.

The password is not here. Preferences are written to userpref.blend in plain
text, so it goes to the Windows Credential Manager through the core's
credentials module instead - the same store the standalone tools use.
'''
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       IntProperty,
                       StringProperty)
from bpy.types import AddonPreferences, Operator, PropertyGroup

from . import core, session


class BB_VolumeMapping(PropertyGroup):
    """One disk, as this machine mounts it."""

    drive: StringProperty(
        name='Drive',
        description='The drive letter a project root is written with, e.g. E:',
        default='',
        update=lambda self, context: _save_volumes(context),
    )
    mount: StringProperty(
        name='Mounted at',
        description='Where this machine mounts that disk, e.g. /Volumes/Misery',
        subtype='DIR_PATH',
        default='',
        update=lambda self, context: _save_volumes(context),
    )


def _table(preferences):
    """The rows as the table the core reads."""
    table = {}
    for row in preferences.volume_list:
        key = (row.drive or '').strip().rstrip(':\\/')
        mount = (row.mount or '').strip()
        if key and mount:
            table[key.upper() + ':'] = mount
    return table


def _save_volumes(context=None):
    """Write the rows through to the settings file, which the core reads."""
    preferences = get(context)
    if preferences is None:
        return
    from .BB_core import settings as core_settings
    core_settings.save({'volumes': _table(preferences)})


def seed_volumes():
    """Fill the rows from the settings file, once, at startup.

    The settings file is the source of truth - the Nuke side and a
    hand-edited install both write it - so the preference rows are an editor
    for it rather than a second copy. Deferred to a timer because the add-on
    preferences do not exist yet while register() is running.
    """
    try:
        preferences = get()
    except Exception:
        return None
    if preferences is None:
        return None

    if len(preferences.volume_list):
        return None

    from .BB_core import settings as core_settings
    for key, mount in sorted((core_settings.get('volumes') or {}).items()):
        row = preferences.volume_list.add()
        row.drive = key
        row.mount = mount
    return None


class BB_OT_volume_add(Operator):
    bl_idname = 'bb.volume_add'
    bl_label = 'Add Volume'
    bl_description = 'Map another drive to where this machine mounts it'
    bl_options = {'INTERNAL'}

    drive: StringProperty(default='')

    def execute(self, context):
        preferences = get(context)
        row = preferences.volume_list.add()
        row.drive = self.drive
        preferences.volume_index = len(preferences.volume_list) - 1
        _save_volumes(context)
        return {'FINISHED'}


class BB_OT_volume_remove(Operator):
    bl_idname = 'bb.volume_remove'
    bl_label = 'Remove Volume'
    bl_description = 'Forget this mapping'
    bl_options = {'INTERNAL'}

    index: IntProperty(default=-1)

    def execute(self, context):
        preferences = get(context)
        if 0 <= self.index < len(preferences.volume_list):
            preferences.volume_list.remove(self.index)
            _save_volumes(context)
        return {'FINISHED'}


class BBPipelinePreferences(AddonPreferences):
    bl_idname = __package__

    server: StringProperty(
        name='Kitsu Server',
        description='Base URL of the Kitsu server, e.g. kitsu.example.com',
        default='',
    )

    email: StringProperty(
        name='Email',
        description='Kitsu login',
        default='',
    )

    allow_insecure_tls: BoolProperty(
        name='Skip Certificate Check',
        description=('Only needed to reach the server by its LAN address. A '
                     'certificate issued for the public hostname will not '
                     'validate against a bare IP. Using the hostname instead '
                     'is the better fix'),
        default=False,
    )

    remember_password: BoolProperty(
        name='Remember Password',
        description=('Store the password in the Windows Credential Manager. '
                     'It is never written to a Blender file or preference'),
        default=True,
    )

    publish_on_save: BoolProperty(
        name='Update Kitsu on Save',
        description=('Post a comment on the Kitsu task each time a version is '
                     'saved from the browser'),
        default=True,
    )

    preview_on_save: BoolProperty(
        name='Attach Viewport Preview',
        description=('Render the viewport and attach it to the comment, which '
                     'Kitsu also uses as the task thumbnail'),
        default=True,
    )

    preview_percentage: IntProperty(
        name='Preview Size',
        description=('Percentage of the scene resolution to render the '
                     'preview at. Kitsu shows it as a thumbnail, so full '
                     'resolution is only slower to upload'),
        default=50, min=10, max=100, subtype='PERCENTAGE',
    )

    kitsu_normalize: BoolProperty(
        name='Let Kitsu Re-encode',
        description=('Allow Zou to conform the upload to the project '
                     'resolution. It upscales anything smaller - a quarter '
                     'resolution test render is stored at full size - and '
                     're-encodes what is already H.264. Off, Kitsu keeps the '
                     'exact file, but builds no separate low-res proxy'),
        default=False,
    )

    review_max_width: IntProperty(
        name='Review Width',
        description=('Longest edge of what gets uploaded to Kitsu. Zero - the '
                     'default - uploads at full show resolution, because '
                     'review is where detail has to be judged. Set a width '
                     'only if a slow link makes full size impractical'),
        default=0, min=0, max=7680,
    )

    still_format: EnumProperty(
        name='Still Format',
        description=('What a single rendered frame is converted to before it '
                     'goes to Kitsu. A sequence always becomes H.264'),
        items=[
            ('PNG', 'PNG', 'Lossless, and what Kitsu stores either way'),
            ('JPEG', 'JPEG',
             'Smaller to upload, but Kitsu re-encodes it to PNG and the '
             'result is both lossy and larger on the server'),
        ],
        default='PNG',
    )

    still_quality: IntProperty(
        name='JPEG Quality',
        description='Quality of the converted still when the format is JPEG',
        default=90, min=10, max=100, subtype='PERCENTAGE',
    )

    frame_range_on_create: BoolProperty(
        name='Set Frame Range on Create',
        description=('Take the shot frame range and the project frame rate '
                     'from Kitsu when a new version is created'),
        default=True,
    )

    frame_range_on_open: EnumProperty(
        name='On Open',
        description='What to do when Kitsu disagrees with an opened scene',
        items=[
            ('WARN', 'Warn', 'Report the difference and offer to fix it'),
            ('APPLY', 'Apply', 'Set the scene to match Kitsu straight away'),
            ('IGNORE', 'Ignore', 'Do not check'),
        ],
        default='WARN',
    )

    generate_previews: BoolProperty(
        name='Generate Preview Icons',
        description=('Render icons for the scenes and collections in the file '
                     'when saving, so they show as thumbnails in the Append '
                     'and Link browsers'),
        default=True,
    )

    volume_list: CollectionProperty(type=BB_VolumeMapping)
    volume_index: IntProperty(default=0)

    work_root: StringProperty(
        name='Work Root',
        description='Where scene files live. Shot folders are built under this',
        subtype='DIR_PATH',
        default='',
    )

    render_root: StringProperty(
        name='Render Root',
        description='Where renders are written. Version folders are built under this',
        subtype='DIR_PATH',
        default='',
    )

    # Where the browser was left. Hidden: these are not settings anybody
    # edits, they are a bookmark. Preferences rather than a file of our own
    # because Blender already persists them, and losing your place between
    # sessions is exactly the annoyance this avoids.
    last_entity_type: StringProperty(default='SHOT', options={'HIDDEN'})
    last_project: StringProperty(default='', options={'HIDDEN'})
    last_sequence: StringProperty(default='', options={'HIDDEN'})
    last_shot: StringProperty(default='', options={'HIDDEN'})
    last_asset_type: StringProperty(default='', options={'HIDDEN'})
    last_asset: StringProperty(default='', options={'HIDDEN'})
    last_task: StringProperty(default='', options={'HIDDEN'})

    config_override: StringProperty(
        name='Config Override',
        description=('Optional config.toml overriding naming and path '
                     'templates. Leave blank to use the built-in defaults'),
        subtype='FILE_PATH',
        default='',
    )

    def draw(self, context):
        layout = self.layout

        if not core.available:
            box = layout.box()
            box.alert = True
            box.label(text=core.error, icon='ERROR')

        column = layout.column()
        column.use_property_split = True

        column.prop(self, 'server')
        column.prop(self, 'email')
        column.prop(self, 'remember_password')

        # There is no password field here on purpose, and people go looking
        # for one, so say where it is rather than leaving them hunting.
        note = column.column(align=True)
        note.label(text='Password is asked for by Kitsu > Connect to Kitsu',
                   icon='INFO')
        note.label(text='It is never written to preferences - only to the '
                        'Windows Credential Manager')

        row = column.row()
        row.alert = self.allow_insecure_tls
        row.prop(self, 'allow_insecure_tls',
                 icon='ERROR' if self.allow_insecure_tls else 'NONE')

        column.separator()
        column.prop(self, 'publish_on_save')
        row = column.row()
        row.enabled = self.publish_on_save
        row.prop(self, 'preview_on_save')
        row = column.row()
        row.enabled = self.publish_on_save and self.preview_on_save
        row.prop(self, 'preview_percentage')

        column.prop(self, 'generate_previews')

        column.prop(self, 'kitsu_normalize')
        column.prop(self, 'review_max_width')
        column.prop(self, 'still_format')
        row = column.row()
        row.enabled = self.still_format == 'JPEG'
        row.prop(self, 'still_quality')

        column.separator()
        column.prop(self, 'frame_range_on_create')
        column.prop(self, 'frame_range_on_open')

        column.separator()
        column.prop(self, 'work_root')
        column.prop(self, 'render_root')

        _draw_volumes(column, self, context)

        for line in kitsu_sources(context):
            note = column.row()
            note.enabled = False
            note.label(text=line, icon='WORLD')

        column.separator()
        column.prop(self, 'config_override')

        if core.available:
            row = column.row()
            row.operator('bb.forget_password', icon='TRASH')


def _draw_volumes(layout, preferences, context):
    r"""The volume table, and what the current roots resolve to through it.

    A list rather than one field: a show is rarely on one disk, and the
    plates, the renders and the work can each be somewhere different. What
    makes this worth a panel at all is that the failure it prevents reads as
    a missing setting - a root written on Windows arrives on a Mac as
    E:\Show, which is not a path there.
    """
    from .BB_core import volumes as core_volumes

    try:
        roots = config(context).paths
    except Exception:
        roots = {}

    wanted = []
    for key in ('work_root', 'render_root'):
        missing = core_volumes.unresolved((roots.get(key) or '').strip())
        if missing and missing not in wanted:
            wanted.append(missing)

    if not wanted and not len(preferences.volume_list):
        return

    box = layout.box()
    header = box.row()
    header.label(text='Volumes', icon='DISK_DRIVE')
    header.operator('bb.volume_add', text='', icon='ADD').drive = ''

    for index, row in enumerate(preferences.volume_list):
        line = box.row(align=True)
        line.prop(row, 'drive', text='')
        line.prop(row, 'mount', text='')
        line.operator('bb.volume_remove', text='', icon='X').index = index

    for missing in wanted:
        note = box.row()
        note.alert = True
        note.label(text='no mapping for %s on this machine' % missing,
                   icon='ERROR')
        # Pre-filled, so the row that is missing is one click rather than a
        # blank the artist has to know how to fill in.
        note.operator('bb.volume_add', text='Map %s' % missing,
                      icon='ADD').drive = missing

    if wanted:
        return

    for key in ('work_root', 'render_root'):
        value = (roots.get(key) or '').strip()
        if not value:
            continue
        here = core_volumes.localise(value)
        if here == value:
            continue
        line = box.row()
        line.enabled = False
        line.label(text='%s here: %s' % (key.replace('_', ' '), here),
                   icon='FILE_FOLDER')


def get(context=None):
    '''The add-on preferences, or None if the add-on is not fully registered.'''
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


# The browser selectors worth putting back next time.
SELECTORS = ('entity_type', 'project', 'sequence', 'shot',
             'asset_type', 'asset', 'task')


def remember(context=None):
    """Bookmark the browser's current selection."""
    preferences = get(context)
    if preferences is None:
        return
    try:
        from . import properties
        props = properties.get(context)
    except Exception:
        return

    # Never bookmark a selection that is mid-rebuild. Clearing the selectors
    # to load a project would otherwise overwrite a good bookmark with the
    # blank state it passes through on the way.
    if properties._suspended:
        return

    for name in SELECTORS:
        value = getattr(props, name, '')
        # 'NONE' is the placeholder an empty dropdown carries; bookmarking it
        # would just overwrite a good bookmark with nothing.
        if value and value != 'NONE':
            setattr(preferences, 'last_' + name, value)


def recall(context=None):
    """What the browser was last pointed at."""
    preferences = get(context)
    if preferences is None:
        return {name: '' for name in SELECTORS}
    return {name: getattr(preferences, 'last_' + name, '') for name in SELECTORS}


def kitsu_sources(context=None):
    """What the selected Kitsu project is contributing, for the UI to show.

    Worth saying out loud: with a brief supplying the roots, the preference
    fields sit empty and look broken until something explains why.
    """
    project = _selected_project(context)
    if not project:
        return []

    from .BB_core import brief, filetree

    lines = []
    tree = filetree.describe(project)
    if tree and 'no file tree' not in tree:
        lines.append(tree)
    said = brief.describe(project)
    if said:
        lines.append(said)
    return lines


def roots_ready(context=None):
    '''``(ok, message)`` for whether the paths are configured enough to act.

    Checked before the browser offers to create anything, so a missing root
    is reported while it can still be fixed, rather than as a failure after
    the user has picked a shot and pressed OK.
    '''
    preferences = get(context)
    if preferences is None:
        return False, 'BB Kitsu Pipeline preferences are unavailable'

    # The preference is not the only source any more: a [bb] block in the
    # Kitsu project brief can supply the roots, and when it does there is
    # nothing for the artist to configure at all.
    try:
        if (config(context).paths.get('work_root') or '').strip():
            return True, ''
    except Exception:
        pass

    if not preferences.work_root:
        # A brief that will not parse is the likeliest reason a project with
        # a root configured still reports one missing.
        from .BB_core import brief
        broken = brief.problem(_selected_project(context))
        if broken:
            return False, broken
        return False, ('Set a Work Root in the add-on preferences, or a '
                       '[bb] block in the Kitsu project brief')
    return True, ''


def config(context=None):
    '''A core Config carrying the roots from preferences.

    Built fresh on each call rather than cached: the roots are preferences the
    user can change at any moment, and a stale root writes files into the
    wrong place silently.
    '''
    preferences = get(context)
    if preferences is None:
        raise RuntimeError('BB Kitsu Pipeline preferences are unavailable')

    from .BB_core import config as config_module

    data = config_module.load(preferences.config_override or None)
    config = session.Config(data).with_roots(
        work_root=bpy.path.abspath(preferences.work_root) if preferences.work_root else '',
        render_root=bpy.path.abspath(preferences.render_root) if preferences.render_root else '',
    )

    # A file tree set on the Kitsu project outranks the local templates: it is
    # the studio's answer to where files go, and every DCC reads the same one.
    # Absent - which is the normal case - nothing changes.
    return config.for_kitsu_project(_selected_project(context))


def _selected_project(context=None):
    '''The Kitsu project dict the browser is pointed at, or None.

    Wrapped because this is called from draw code and during registration,
    when the window manager may not carry the browser properties yet.
    '''
    try:
        from . import properties
        return session.state.project(properties.get(context).project)
    except Exception:
        return None


classes = (BB_VolumeMapping, BB_OT_volume_add, BB_OT_volume_remove,
           BBPipelinePreferences)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    # The rows are an editor for the settings file, so they are filled from
    # it once the preferences exist - which is not yet.
    bpy.app.timers.register(seed_volumes, first_interval=0.1)


def unregister():
    if bpy.app.timers.is_registered(seed_volumes):
        bpy.app.timers.unregister(seed_volumes)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
