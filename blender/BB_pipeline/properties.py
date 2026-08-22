'''Browser properties and the cascading dropdowns.

A Kitsu project holds two trees and the browser shows them as two tabs, so the
selectors come in pairs: sequence/shot for the Shots tab, asset type/asset for
the Assets tab. Each tab keeps its own selection, so switching back and forth
does not lose your place.

Every selector is labelled. Unlabelled they read as one flat chain, when the
middle two actually name different kinds of thing depending on which tab you
are in.

Two rules keep them cheap:

  * Enum item callbacks only read what the session already has. They are
    called on every redraw, so a network request in one would make the whole
    UI stutter.
  * Selecting a project fetches its sequences, shots, asset types and assets
    at once. Narrowing after that is a local filter, not a round trip.

Item lists are kept in module globals because Blender does not own the strings
a dynamic enum callback returns - hand it a temporary list and the labels turn
to garbage as soon as Python frees it.
'''
import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import PropertyGroup

from . import session

EMPTY = [('NONE', '', '')]

# Live references for the enum callbacks; see the module docstring.
_project_items = list(EMPTY)
_sequence_items = list(EMPTY)
_shot_items = list(EMPTY)
_asset_type_items = list(EMPTY)
_asset_items = list(EMPTY)
_task_items = list(EMPTY)
_version_items = list(EMPTY)
_status_items = list(EMPTY)


def _valid(identifier):
    return bool(identifier) and identifier != 'NONE'


# Set while the selectors are being written to programmatically. Opening a file
# restores all of them at once, and letting each one's update callback fire
# would have the project reset the sequence that the next line is about to set.
_suspended = False


class suspend_updates:
    '''Context manager silencing the cascade while selectors are restored.'''

    def __enter__(self):
        global _suspended
        _suspended = True
        return self

    def __exit__(self, *exception):
        global _suspended
        _suspended = False
        return False


def clear(props, *names):
    '''Reset selectors, tolerating a value that is not currently legal.

    ``'NONE'`` is only a member of a dynamic enum while its list is empty, so
    assigning it raises as soon as the list has real entries. Blender already
    coerces an out-of-range enum to its first item, which is the behaviour
    wanted here anyway, so the failure is genuinely nothing to handle.
    '''
    for name in names:
        try:
            setattr(props, name, 'NONE')
        except TypeError:
            pass


# -- enum item callbacks -------------------------------------------------------

def project_items(self, context):
    global _project_items
    _project_items = [
        (project['id'], project.get('name', '?'), project.get('code') or '')
        for project in session.state.projects
    ] or list(EMPTY)
    return _project_items


def sequence_items(self, context):
    global _sequence_items
    _sequence_items = [
        (sequence['id'], sequence.get('name', '?'),
         'Sequence %s' % sequence.get('name', ''))
        for sequence in session.state.sequences
    ] or list(EMPTY)
    return _sequence_items


def shot_items(self, context):
    global _shot_items
    state = session.state
    props = get(context)

    shots = state.shots_in(props.sequence) if _valid(props.sequence) else []
    _shot_items = [
        (shot['id'], shot.get('name', '?'), shot.get('description') or '')
        for shot in shots
    ] or list(EMPTY)
    return _shot_items


def asset_type_items(self, context):
    global _asset_type_items
    _asset_type_items = [
        (asset_type['id'], asset_type.get('name', '?'),
         'Asset type %s' % asset_type.get('name', ''))
        for asset_type in session.state.asset_types
    ] or list(EMPTY)
    return _asset_type_items


def asset_items(self, context):
    global _asset_items
    state = session.state
    props = get(context)

    assets = state.assets_of(props.asset_type) if _valid(props.asset_type) else []
    _asset_items = [
        (asset['id'], asset.get('name', '?'), asset.get('description') or '')
        for asset in assets
    ] or list(EMPTY)
    return _asset_items


def task_items(self, context):
    '''Tasks on the selected entity that belong to this DCC.

    Two filters, both deliberate. Only tasks the entity actually has, because
    a task type it does not have is not somewhere work can be published. And
    only the departments configured for Blender - a compositing task has no
    business being openable as a .blend, and Nuke will apply the same rule to
    its own list.
    '''
    global _task_items
    state = session.state
    allowed = state.task_departments

    entries = []
    for task in state.tasks:
        task_type_id = task.get('task_type_id', '')
        name = state.task_type_name(task_type_id)
        if not name:
            continue
        if allowed is not None:
            department = state.department_of(task_type_id)
            if department.lower() not in allowed:
                continue
        entries.append((task['id'], name, 'Task %s' % name))

    _task_items = sorted(entries, key=lambda item: item[1].lower()) or list(EMPTY)
    return _task_items


def version_items(self, context):
    '''Scene files on disk for the current task, newest first.'''
    global _version_items
    _version_items = [
        (str(version), 'v%03d' % version, path.name)
        for version, path in reversed(session.state.workfiles)
    ] or [('NONE', 'no scene file yet', '')]
    return _version_items


KEEP_STATUS = 'KEEP'


def status_items(self, context):
    '''Kitsu task statuses, with "leave unchanged" first.

    First and default on purpose. A save is not a submission, so the status
    only moves when someone deliberately picks a new one.
    '''
    global _status_items
    _status_items = [
        (KEEP_STATUS, 'Leave unchanged', 'Keep the status the task already has'),
    ] + [
        (status['id'], status.get('name', '?'), status.get('short_name') or '')
        for status in session.state.statuses
    ]
    return _status_items


# -- selection changes ---------------------------------------------------------

def _on_entity_type(self, context):
    if _suspended:
        return
    from . import fetch, prefs
    fetch.entity_selected(context)
    prefs.remember(context)


def _on_project(self, context):
    if _suspended:
        return
    from . import fetch, prefs
    fetch.project_selected(context)
    prefs.remember(context)


def _on_group(self, context):
    '''Sequence or asset type changed; the level below is already cached.'''
    if _suspended:
        return
    from . import fetch, prefs
    session.state.tasks = []
    with suspend_updates():
        clear(self, 'shot' if self.entity_type == 'SHOT' else 'asset', 'task')
    fetch.entity_selected(context)
    prefs.remember(context)


def _on_entity(self, context):
    if _suspended:
        return
    from . import fetch, prefs
    fetch.entity_selected(context)
    prefs.remember(context)


def _on_task(self, context):
    if _suspended:
        return
    from . import fetch, prefs
    fetch.refresh_workfiles(context)
    prefs.remember(context)


# -- property group ------------------------------------------------------------

class BB_BrowserProperties(PropertyGroup):
    password: StringProperty(
        name='Password',
        description=('Kitsu password. Held for this session only; tick '
                     'Remember Password in preferences to keep it in the '
                     'Windows Credential Manager'),
        subtype='PASSWORD',
        default='',
    )

    entity_type: EnumProperty(
        name='Type',
        items=[
            ('ASSET', 'Assets', 'Browse the project assets', 'OUTLINER_OB_GROUP_INSTANCE', 0),
            ('SHOT', 'Shots', 'Browse the project sequences and shots', 'SEQUENCE', 1),
        ],
        default='SHOT',
        update=_on_entity_type,
    )

    project: EnumProperty(name='Project', items=project_items, update=_on_project)

    sequence: EnumProperty(name='Sequence', items=sequence_items, update=_on_group)
    shot: EnumProperty(name='Shot', items=shot_items, update=_on_entity)

    asset_type: EnumProperty(name='Asset Type', items=asset_type_items, update=_on_group)
    asset: EnumProperty(name='Asset', items=asset_items, update=_on_entity)

    task: EnumProperty(name='Task', items=task_items, update=_on_task)
    version: EnumProperty(name='Version', items=version_items)

    comment: StringProperty(
        name='Comment',
        description='Posted on the Kitsu task with this version',
        default='',
    )

    task_status: EnumProperty(
        name='Status',
        description='Task status to set, or leave it as it is',
        items=status_items,
    )

    action: EnumProperty(
        name='Action',
        items=[
            ('OPEN', 'Open Version', 'Open the selected scene file', 'FILE_FOLDER', 0),
            ('NEW', 'New Version', 'Create the next version for this task', 'FILE_NEW', 1),
        ],
        default='OPEN',
    )

    # -- whichever tab is showing ---------------------------------------------

    @property
    def is_asset(self):
        return self.entity_type == 'ASSET'

    @property
    def group_id(self):
        return self.asset_type if self.is_asset else self.sequence

    @property
    def entity_id(self):
        return self.asset if self.is_asset else self.shot


def get(context=None):
    context = context or bpy.context
    return context.window_manager.bb_browser


def clear_workfiles(context=None):
    session.state.workfiles = []
    clear(get(context), 'version')


def selected_status_id(context=None):
    '''The chosen status id, or None to leave the task's status alone.'''
    value = get(context).task_status
    return None if value in ('', KEEP_STATUS, 'NONE') else value


def selected_version(context=None):
    '''``(version, Path)`` for the chosen file, or None.'''
    props = get(context)
    if not _valid(props.version):
        return None
    wanted = int(props.version)
    return next((entry for entry in session.state.workfiles if entry[0] == wanted), None)


classes = (BB_BrowserProperties,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.bb_browser = bpy.props.PointerProperty(
        type=BB_BrowserProperties)


def unregister():
    del bpy.types.WindowManager.bb_browser
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
