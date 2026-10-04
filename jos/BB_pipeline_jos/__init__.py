'''BB Kitsu Pipeline - JOS Compositor integration.

The Nuke integration's twin, through JOS's embedded Python (the ``jos``
module, docs/pipeline-scripting.md in JOS). Naming, paths, versioning and
Kitsu access all live in the shared BB_core package; this supplies the JOS
menus and the calls into ``jos``.

JOS browses **shots only** and offers **compositing tasks only**, as Nuke
does: the task filter is the department list under ``[dcc.jos]`` in the
config.
'''
import os

from . import core

# Has to happen before the submodules that import BB_core are touched.
core.bootstrap()

__version__ = '1.2.0'

MENU = 'Kitsu'

_installed = False


def install_menu():
    '''Add the Kitsu menu to JOS's menu bar (once: JOS has no menu removal).'''
    global _installed
    import jos

    if not core.available:
        print('BB Kitsu Pipeline: %s' % core.error)
        return None
    if _installed or not jos.has_ui():
        return None
    _installed = True

    menu = jos.menu(MENU)
    menu.add_command('Browser...', open_browser, 'Ctrl+Alt+B')
    menu.add_separator()
    menu.add_command('Create Write Node', create_write, 'Ctrl+Alt+W')
    menu.add_command('Save Next Version', save_next_version, 'Ctrl+Alt+S')
    menu.add_command('Update Kitsu...', update_kitsu)
    menu.add_separator()
    menu.add_command('Settings...', open_settings)

    # Printed so it is possible to tell which build JOS actually loaded.
    print('BB Kitsu Pipeline %s loaded from %s'
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
    '''Ask first, then version up the open project and do what was ticked.

    Asked before anything is written: Cancel leaves the disk as it was.
    '''
    import jos

    from . import browser, scripts, stamp

    try:
        upcoming, version = scripts.upcoming_version()
    except scripts.ScriptError as error:
        jos.message(str(error))
        return ''

    entity_context = stamp.read_current()[0]
    choices = browser.ask_save_choices(
        entity_context.at_version(version), upcoming,
        title='Save Next Version', ok_text='Save v%03d' % version)
    if choices is None:
        return ''

    try:
        path = scripts.save_next_version()
    except scripts.ScriptError as error:
        jos.message(str(error))
        return ''

    print('[Kitsu] saved %s' % path)
    browser.carry_out(choices, stamp.read_current()[0], path)
    return path


def create_write():
    """A Write pointed at this version's render path, with Kitsu buttons."""
    import jos

    from . import writenode

    try:
        node = writenode.create()
    except writenode.WriteError as error:
        jos.message(str(error))
        return None

    print('[BB] %s -> %s' % (node.name, node['file']))
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
    import jos

    from . import browser, review, writenode

    path = writenode.pattern_of(node)
    found = review.rendered_frames(path)
    if not found:
        jos.message('Nothing rendered there yet:' + chr(10) + chr(10) + str(path))
        return ''

    return browser.ask_to_publish_render(node, len(found))


def update_kitsu():
    '''Post a comment and status for the project already open.'''
    import jos

    from . import browser, scripts

    path = scripts.current_path()
    if not path:
        jos.message('Save this project first')
        return
    browser.ask_to_publish(path)
