'''A Write node that knows where it belongs.

An ordinary Nuke Write with a Kitsu tab added, rather than a gizmo wrapping
one. Everything native stays native - channels, file type, compression,
colourspace, views, all of it - because a comper who cannot reach the usual
Write settings will just build a normal Write instead and the pipeline loses
track of the render entirely.

What the tab adds is the three things a Write cannot do on its own:

* the output path, derived from the script's own context and version rather
  than typed in - the rule the rest of the pipeline hangs on;
* a Read of what was just rendered, because checking a render by hand-typing
  the same path a second time is how the wrong version gets reviewed;
* a publish, which turns whatever was rendered into something Kitsu can play.

The buttons are PyScript knobs, so they are saved inside the .nk and keep
working in a script somebody else opens - as long as they have this package.
'''
import os

from BB_core import settings, workfiles

from . import session, stamp

state = session.state

TAB = 'bb_kitsu'
STREAM_KNOB = 'bb_stream'
INFO_KNOB = 'bb_info'

# Reaching back into the package by name, because the knob text is stored in
# the .nk and has to work in a session where nothing is imported yet.
CALL = 'import BB_pipeline_nuke as _bb; _bb.%s(nuke.thisNode())'


class WriteError(Exception):
    '''Something the artist needs to fix before this can work.'''


def _nuke():
    import nuke
    return nuke


def is_ours(node):
    try:
        return node.knob(STREAM_KNOB) is not None
    except Exception:
        return False


def stream_of(node):
    knob = node.knob(STREAM_KNOB)
    return (knob.value() if knob else '') or 'main'


def output_path(entity_context, stream='main'):
    '''Where this stream of this version renders to, as a frame pattern.'''
    if entity_context is None or not entity_context.is_complete():
        raise WriteError('This script carries no Kitsu context - create a '
                         'version from the Kitsu browser first')
    if not entity_context.version:
        raise WriteError('This script has no version to render under')

    config = session.config_for(entity_context)
    if not (config.paths.get('render_root') or '').strip():
        raise WriteError('Set a Render Root in Kitsu > Settings..., or a '
                         '[bb] block in the Kitsu project brief')

    # The core builds the same path Blender renders to; %04d is what Nuke
    # wants where the core writes ####.
    path = workfiles.render_file(entity_context, stream, frame='%04d', config=config)
    return str(path).replace('\\', '/')


def set_output_path(node):
    '''Point a Write at the path its script's version should render to.'''
    nuke = _nuke()

    entity_context, _source = stamp.read_current()
    try:
        path = output_path(entity_context, stream_of(node))
    except WriteError as error:
        nuke.message(str(error))
        return ''

    node.knob('file').setValue(path)

    info = node.knob(INFO_KNOB)
    if info is not None:
        info.setValue(entity_context.versioned())
    return path


def create(stream='main'):
    '''Build a Write for the open script's current version.'''
    nuke = _nuke()

    entity_context, _source = stamp.read_current()
    path = output_path(entity_context, stream)      # raises before anything is made

    selected = None
    try:
        chosen = nuke.selectedNodes()
        if len(chosen) == 1:
            selected = chosen[0]
    except Exception:
        selected = None

    knobs = {'file': path, 'file_type': 'exr',
             'create_directories': True}
    node = (nuke.nodes.Write(inputs=[selected], **knobs) if selected
            else nuke.nodes.Write(**knobs))
    node.setName('KitsuWrite1', uncollide=True)

    _add_tab(node, stream, entity_context)
    return node


def _add_tab(node, stream, entity_context):
    nuke = _nuke()

    tab = nuke.Tab_Knob(TAB, 'Kitsu')
    node.addKnob(tab)

    marker = nuke.String_Knob(STREAM_KNOB, 'Stream')
    marker.setValue(stream)
    marker.setFlag(nuke.INVISIBLE)
    node.addKnob(marker)

    info = nuke.Text_Knob(INFO_KNOB, 'Version')
    info.setValue(entity_context.versioned() if entity_context else '')
    node.addKnob(info)

    node.addKnob(nuke.PyScript_Knob(
        'bb_set_path', 'Set Output Path', CALL % 'write_set_path'))
    node.addKnob(nuke.PyScript_Knob(
        'bb_add_read', 'Add Read Node', CALL % 'write_add_read'))
    node.addKnob(nuke.PyScript_Knob(
        'bb_publish', 'Publish to Kitsu', CALL % 'write_publish'))

    note = nuke.Text_Knob('bb_note', '')
    note.setValue('Publish converts whatever was rendered into H.264 for Kitsu.')
    node.addKnob(note)


def pattern_of(node):
    """The Write's path as a frame *pattern*.

    Deliberately the knob's raw value rather than ``evaluate()``. Evaluating a
    File_Knob substitutes the current frame, so it hands back one concrete
    filename - which globs to nothing whenever the playhead is not sitting on
    a frame that was rendered. That is what made a finished render report
    "nothing rendered yet".
    """
    knob = node.knob('file')
    if knob is None:
        return ''

    raw = knob.value() or ''
    from . import review
    if review.has_frame_pattern(raw):
        return raw

    # No placeholder in the raw value - an expression, or a single-file
    # output. Evaluating is the right answer for those.
    try:
        return knob.evaluate() or raw
    except Exception:
        return raw


def add_read(node):
    '''Create a Read of what this Write rendered, wired below it.'''
    nuke = _nuke()

    from . import review

    path = pattern_of(node)
    if not path:
        nuke.message('This Write has no output path yet - press Set Output Path')
        return None

    found = review.rendered_frames(path)
    if not found:
        nuke.message('Nothing rendered there yet:\n\n%s' % path)
        return None

    first, last = review.frame_span(path, found)
    read = nuke.nodes.Read(file=path, first=first, last=last, origfirst=first,
                           origlast=last)
    try:
        read.setXYpos(node.xpos(), node.ypos() + 100)
    except Exception:
        pass

    read.setName('KitsuRead1', uncollide=True)
    return read
