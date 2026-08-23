'''The browser's left pane: one expandable tree instead of three selectors.

Blender has no tree widget, so this builds the rows itself - a flat list with
a depth on each - and draws them as indented buttons. Clicking a row sets the
same ``sequence`` / ``shot`` / ``task`` properties the old dropdowns set, so
everything downstream (the update callbacks, the bookmark, the fetches) is
untouched; only the way the choice is made has changed.

Tasks hang under the selected entity alone. Kitsu serves tasks per entity, so
a tree that showed them under every shot would cost one request per shot on
every redraw. Expanding a shot selects it, which is what fetches its tasks -
the lazy load and the selection are the same gesture.
'''
import bpy
from bpy.types import Operator

from . import fetch, properties, session

state = session.state

# Which groups are open. Not a property: the browser is a popup, the set dies
# with the session, and Blender has no set-typed property anyway.
_expanded = set()


def is_expanded(group_id):
    return group_id in _expanded


def toggle(group_id):
    _expanded.discard(group_id) if group_id in _expanded else _expanded.add(group_id)


def reset():
    _expanded.clear()


def reveal(props):
    '''Open the branches leading to what is currently selected.'''
    if props.group_id:
        _expanded.add(props.group_id)


def rows(props):
    '''The visible tree, as ``(kind, id, label, depth, selected)`` tuples.

    ``kind`` is 'group', 'entity' or 'task' - the same two-level vocabulary
    the rest of the add-on uses, so this works for the Assets tab and the
    Shots tab without knowing which one it is drawing.
    '''
    is_asset = props.is_asset
    groups = state.asset_types if is_asset else state.sequences
    built = []

    for group in groups:
        group_id = group['id']
        selected = group_id == props.group_id
        built.append(('group', group_id, group.get('name') or '?', 0, selected))

        if group_id not in _expanded:
            continue

        entities = (state.assets_of(group_id) if is_asset
                    else state.shots_in(group_id))
        for entity in entities:
            entity_id = entity['id']
            chosen = entity_id == props.entity_id
            built.append(('entity', entity_id, entity.get('name') or '?', 1, chosen))

            if not chosen:
                continue

            # Only the selected entity has its tasks loaded, and only the
            # departments this DCC cares about survive the filter - which is
            # why lighting and FX never appear in Blender's tree.
            for task in state.tasks:
                task_id = task['id']
                built.append(('task', task_id,
                              state.task_type_name(task['task_type_id']) or '?',
                              2, task_id == props.task))

    return built


def _parent_of(props, entity_id):
    '''The group an entity hangs under, whichever tab is showing.'''
    if props.is_asset:
        asset = state.asset(entity_id) or {}
        return asset.get('entity_type_id') or ''
    shot = state.shot(entity_id) or {}
    return shot.get('parent_id') or ''


def _pick(props, kind, identifier):
    '''Point the existing properties at what was clicked.

    Expanding a group deliberately changes nothing but the tree. A dropdown
    could only ever be *changed*, so selecting and browsing were the same
    act; a tree lets you open a branch to look inside it, and making that
    select the branch too rewrote the bookmark to a sequence the remembered
    shot does not belong to - an impossible pair that then failed to restore.
    '''
    if kind == 'group':
        toggle(identifier)
        return

    if kind == 'entity':
        # The group has to lead, because the entity list is filtered by it -
        # assigning a shot from another sequence to the enum would not take.
        parent = _parent_of(props, identifier)
        if parent and parent != props.group_id:
            if props.is_asset:
                props.asset_type = parent
            else:
                props.sequence = parent
        if props.is_asset:
            props.asset = identifier
        else:
            props.shot = identifier
    elif kind == 'task':
        props.task = identifier
    elif kind == 'version':
        props.version = identifier


class BB_OT_tree_pick(Operator):
    bl_idname = 'bb.tree_pick'
    bl_label = 'Select'
    bl_description = 'Select this item'
    bl_options = {'INTERNAL'}

    kind: bpy.props.StringProperty()
    identifier: bpy.props.StringProperty()

    def execute(self, context):
        props = properties.get(context)
        # A row for something Kitsu has since dropped would raise on assign;
        # the browser is redrawn from live state, so ignoring it is enough.
        try:
            _pick(props, self.kind, self.identifier)
        except TypeError:
            return {'CANCELLED'}
        return {'FINISHED'}


def draw(layout, props):
    '''The tree, into whichever column the browser hands over.'''
    listing = rows(props)
    if not listing:
        empty = layout.row()
        empty.enabled = False
        empty.label(text='nothing here yet', icon='INFO')
        return

    column = layout.column(align=True)
    for kind, identifier, label, depth, selected in listing:
        row = column.row(align=True)
        if depth:
            row.separator(factor=depth * 1.6)

        if kind == 'group':
            icon = 'TRIA_DOWN' if identifier in _expanded else 'TRIA_RIGHT'
        elif kind == 'entity':
            icon = 'SEQUENCE' if not props.is_asset else 'OBJECT_DATA'
        else:
            icon = 'DOT'

        button = row.operator('bb.tree_pick', text=label, icon=icon,
                              emboss=selected, depress=selected)
        button.kind = kind
        button.identifier = identifier


classes = (BB_OT_tree_pick,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    reset()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
