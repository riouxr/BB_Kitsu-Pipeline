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

__version__ = '0.1.0'

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
    menu.addCommand('Save Next Version', _run('save_next_version()'), 'ctrl+alt+s')
    menu.addCommand('Update Kitsu...', _run('update_kitsu()'))
    menu.addSeparator()
    menu.addCommand('Settings...', _run('open_settings()'))

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


def update_kitsu():
    '''Post a comment and status for the script already open.'''
    import nuke

    from . import browser, scripts

    path = scripts.current_path()
    if not path:
        nuke.message('Save this script first')
        return
    browser.ask_to_publish(path)
