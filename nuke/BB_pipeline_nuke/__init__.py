'''BB Kitsu Pipeline - Nuke integration.

A thin host-side wrapper, the same shape as the Blender add-on. Naming, paths,
versioning and Kitsu access all live in the shared BB_core package; this
supplies the Nuke UI and the calls into ``nuke``.

Nuke browses **shots only** and offers **compositing tasks only**. An asset
has no comp, and a lighting task has no business opening as a .nk - the task
filter is the department list in the config, the same mechanism Blender uses
to show only its 3D tasks.
'''
import os

from . import core

# Has to happen before the submodules that import BB_core are touched.
core.bootstrap()

__version__ = '0.9.3'

MENU = 'Kitsu'


def _run(statement):
    return 'import BB_pipeline_nuke as _fp; _fp.%s' % statement


def install_menu():
    '''Add the Kitsu menu to Nuke's menu bar. Safe to call twice.'''
    import nuke

    if not core.available:
        nuke.tprint('BB Kitsu Pipeline: %s' % core.error)
        return None

    bar = nuke.menu('Nuke')
    menu = bar.addMenu(MENU)

    # addMenu returns the existing one when it is already there, so rebuilding
    # would double every entry. Clearing first makes a reload idempotent.
    for item in list(menu.items()):
        try:
            menu.removeItem(item.name())
        except Exception:
            pass

    menu.addCommand('Browser...', _run('open_browser()'), 'ctrl+alt+b')
    menu.addSeparator()
    menu.addCommand('Create Write Node', _run('create_write()'))
    menu.addCommand('Save Next Version', _run('save_next_version()'), 'ctrl+alt+s')
    menu.addCommand('Update Kitsu...', _run('update_kitsu()'))
    menu.addSeparator()
    menu.addCommand('Settings...', _run('open_settings()'))

    # Also in the node menu, which is what the Tab key searches. A node that
    # cannot be made the way every other node is made will not get used.
    nodes = nuke.menu('Nodes')
    kitsu_nodes = nodes.addMenu(MENU)
    for item in list(kitsu_nodes.items()):
        try:
            kitsu_nodes.removeItem(item.name())
        except Exception:
            pass
    kitsu_nodes.addCommand('Write Kitsu', _run('create_write()'), 'ctrl+alt+w')

    # Printed so it is possible to tell which build Nuke actually loaded -
    # Nuke caches imported modules, so an unrestarted session runs old code
    # and looks identical from the menu.
    nuke.tprint('BB Kitsu Pipeline %s loaded from %s'
                % (__version__, os.path.dirname(os.path.dirname(__file__))))
    return menu


# -- what the menu entries call ----------------------------------------------

def open_browser():
    from . import browser
    return browser.show_browser()


def open_settings():
    from . import browser
    return browser.show_settings()


def save_next_version():
    '''Version up the open script, then offer to tell Kitsu.'''
    import nuke

    from . import browser, scripts

    try:
        path = scripts.save_next_version()
    except scripts.ScriptError as error:
        nuke.message(str(error))
        return ''

    nuke.tprint('[Kitsu] saved %s' % path)
    browser.ask_to_publish(path)
    return path


def create_write():
    """A Write pointed at this version's render path, with a Kitsu tab."""
    import nuke

    from . import writenode

    try:
        node = writenode.create()
    except writenode.WriteError as error:
        nuke.message(str(error))
        return None

    nuke.tprint('[BB] %s -> %s' % (node.name(), node.knob('file').value()))
    return node


# -- what the buttons on a Kitsu Write call ----------------------------------

def write_set_path(node):
    from . import writenode
    return writenode.set_output_path(node)


def write_add_read(node):
    from . import writenode
    return writenode.add_read(node)


def write_publish(node):
    """Build a review movie from what this Write rendered, and send it."""
    import nuke

    from . import browser, review, writenode

    path = writenode.pattern_of(node)
    found = review.rendered_frames(path)
    if not found:
        nuke.message('Nothing rendered there yet:' + chr(10) + chr(10) + str(path))
        return ''

    return browser.ask_to_publish_render(node, len(found))


def update_kitsu():
    '''Post a comment and status for the script already open.'''
    import nuke

    from . import browser, scripts

    path = scripts.current_path()
    if not path:
        nuke.message('Save this script first')
        return
    browser.ask_to_publish(path)
