'''A Write node that knows where it belongs.

An ordinary JOS Write with Kitsu parameters added, as on the Nuke side:
everything native stays native - channels, file type, compression, colour -
because a comper who cannot reach the usual Write settings will just build a
normal Write instead and the pipeline loses track of the render.

What the parameters add is the three things a Write cannot do on its own:

* the output path, derived from the project's own context and version;
* a Read of what was just rendered;
* a publish, which turns whatever was rendered into something Kitsu can play.

The buttons are JOS button parameters (Nuke's PyScript knobs): their Python is
saved inside the .joscomp and runs when clicked, as long as this plugin is
installed.
'''
from BB_core import workfiles

from . import session, stamp

state = session.state

STREAM_KNOB = 'bb_stream'
INFO_KNOB = 'bb_info'

# Reaching back into the package by name, because the button's code is stored
# in the project and has to work in a session where nothing is imported yet.
CALL = 'import jos, BB_pipeline_jos as _bb; _bb.%s(jos.this_node())'


class WriteError(Exception):
    '''Something the artist needs to fix before this can work.'''


def _jos():
    import jos
    return jos


def is_ours(node):
    try:
        return STREAM_KNOB in node
    except Exception:
        return False


def stream_of(node):
    return (node[STREAM_KNOB] if is_ours(node) else '') or 'main'


def output_path(entity_context, stream='main'):
    '''Where this stream of this version renders to, as a frame pattern.'''
    if entity_context is None or not entity_context.is_complete():
        raise WriteError('This project carries no Kitsu context - create a '
                         'version from the Kitsu browser first')
    if not entity_context.version:
        raise WriteError('This project has no version to render under')

    config = session.config_for(entity_context)
    if not (config.paths.get('render_root') or '').strip():
        from BB_core import brief
        raise WriteError(brief.missing_root(
            session.project_of(entity_context), 'render root'))

    # The core builds the same path Blender and Nuke render to; JOS reads
    # %04d as Nuke does.
    path = workfiles.render_file(entity_context, stream, frame='%04d',
                                 config=config, dcc=session.DCC)
    return str(path).replace('\\', '/')


def set_output_path(node, quiet=False):
    '''Point a Write at the path its project's version should render to.'''
    entity_context, _source = stamp.read_current()
    try:
        path = output_path(entity_context, stream_of(node))
    except WriteError as error:
        if quiet:
            raise
        _jos().message(str(error))
        return ''

    node['file'] = path
    if INFO_KNOB in node:
        node[INFO_KNOB] = entity_context.versioned()
    return path


def create(stream='main'):
    '''Build a Write for the open project's current version.'''
    jos = _jos()

    entity_context, _source = stamp.read_current()
    path = output_path(entity_context, stream)      # raises before anything is made

    chosen = jos.selected_nodes()
    selected = chosen[0] if len(chosen) == 1 else None

    with jos.undo_group('Create Kitsu Write'):
        where = (selected.x, selected.y + 100) if selected else (0.0, 0.0)
        node = jos.create_node('Write', name='KitsuWrite1', x=where[0],
                               y=where[1], file=path, file_type='exr',
                               create_directories=True)
        if selected is not None:
            node.set_input(0, selected)
        _add_params(node, stream, entity_context)
    jos.refresh()
    return node


def _add_params(node, stream, entity_context):
    node.add_param(STREAM_KNOB, 'string', stream, label='Stream', hidden=True)
    node.add_param(INFO_KNOB, 'string',
                   entity_context.versioned() if entity_context else '',
                   label='Kitsu version',
                   help='The Kitsu version this Write renders (set by Set '
                        'Output Path).')
    node.add_param('bb_set_path', 'button', CALL % 'write_set_path',
                   label='Set Output Path',
                   help="Points the Write at this version's render folder.")
    node.add_param('bb_add_read', 'button', CALL % 'write_add_read',
                   label='Add Read Node',
                   help='A Read of what this Write rendered.')
    node.add_param('bb_publish', 'button', CALL % 'write_publish',
                   label='Publish to Kitsu',
                   help='Converts whatever was rendered into H.264 for Kitsu.')


def pattern_of(node):
    """The Write's path as a frame pattern (its raw value, never one frame)."""
    try:
        return node['file'] or ''
    except Exception:
        return ''


def add_read(node):
    '''Create a Read of what this Write rendered, below it.'''
    jos = _jos()

    from . import review

    path = pattern_of(node)
    if not path:
        jos.message('This Write has no output path yet - press Set Output Path')
        return None

    found = review.rendered_frames(path)
    if not found:
        jos.message('Nothing rendered there yet:\n\n%s' % path)
        return None

    first, last = review.frame_span(path, found)
    read = jos.create_node('Read', name='KitsuRead1', x=node.x,
                           y=node.y + 100, file=path, first=first, last=last)
    jos.refresh()
    return read
