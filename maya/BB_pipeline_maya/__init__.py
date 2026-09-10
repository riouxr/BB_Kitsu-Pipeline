'''BB Kitsu Pipeline - Maya integration.

A thin host-side wrapper, the same shape as the Blender add-on and the Nuke
package. Naming, paths, versioning and Kitsu access all live in the shared
BB_core package; this supplies the Maya UI and the calls into ``maya.cmds``.

Maya browses **shots and assets**, the way Blender does - it models, rigs,
animates and lights, and half of that work is assets. Which tasks it offers to
*author* is the department list in the config; tasks it does not author still
appear, because a lighter has every reason to look at what rigging produced.
'''
import os

from . import core

# Has to happen before the submodules that import BB_core are touched.
core.bootstrap()

__version__ = '1.1.0'

MENU = 'Kitsu'
_MENU_NAME = 'bbKitsuMenu'


def _cmds():
    import maya.cmds as cmds
    return cmds


def install_menu():
    '''Add the Kitsu menu to Maya's menu bar. Safe to call twice.

    Returns the menu name, or None when there is no interface to add it to -
    which is the normal case under mayapy and not an error.
    '''
    cmds = _cmds()

    if not core.available:
        print('BB Kitsu Pipeline: %s' % core.error)
        return None

    try:
        batch = cmds.about(batch=True)
    except Exception:
        batch = True
    if batch:
        return None

    # Deleted and rebuilt rather than added to, so reloading the package
    # during development does not leave two menus side by side.
    if cmds.menu(_MENU_NAME, exists=True):
        cmds.deleteUI(_MENU_NAME, menu=True)

    menu = cmds.menu(_MENU_NAME, parent='MayaWindow', label=MENU, tearOff=True)

    cmds.menuItem(parent=menu, label='Browser...',
                  command=_run('open_browser()'))
    cmds.menuItem(parent=menu, divider=True)
    cmds.menuItem(parent=menu, label='Save Next Version',
                  command=_run('save_next_version()'))
    cmds.menuItem(parent=menu, label='Update Kitsu...',
                  command=_run('update_kitsu()'))
    cmds.menuItem(parent=menu, label='Publish Render Frame...',
                  command=_run('publish_render()'))
    cmds.menuItem(parent=menu, divider=True)
    cmds.menuItem(parent=menu, label='Set Output Path',
                  command=_run('set_output_path()'))
    cmds.menuItem(parent=menu, label='Apply Kitsu Frame Range',
                  command=_run('apply_frame_range()'))
    cmds.menuItem(parent=menu, label='Open Work Folder',
                  command=_run('open_work_folder()'))
    cmds.menuItem(parent=menu, divider=True)
    cmds.menuItem(parent=menu, label='Settings...',
                  command=_run('open_settings()'))

    # Printed so it is possible to tell which build Maya actually loaded -
    # Maya caches imported modules, so an unrestarted session runs old code
    # and looks identical from the menu.
    print('BB Kitsu Pipeline %s loaded from %s'
          % (__version__, os.path.dirname(os.path.dirname(__file__))))
    return menu


def _run(statement):
    '''A menuItem command string that survives a module reload.

    Maya stores the string and evaluates it at click time, so importing here
    rather than capturing a function object means an edited module is picked
    up by the next click rather than by the next restart.
    '''
    return 'import BB_pipeline_maya as _bb; _bb.%s' % statement


def remove_menu():
    '''Take the menu away again, for an uninstall or a clean reload.'''
    cmds = _cmds()
    try:
        if cmds.menu(_MENU_NAME, exists=True):
            cmds.deleteUI(_MENU_NAME, menu=True)
    except Exception:
        pass


# -- what the menu entries call ----------------------------------------------

def open_browser(*_args):
    from . import browser
    return browser.show_browser()


def open_settings(*_args):
    from . import browser
    return browser.show_settings()


def save_next_version(*_args):
    '''Ask first, then version up the open scene and do what was ticked.

    Asked before anything is written: the version is only claimed, the scene
    only saved and the master only replaced once OK is pressed, so Cancel
    leaves the disk exactly as it was.
    '''
    from . import browser, scenes, stamp

    try:
        upcoming, version = scenes.upcoming_version()
    except scenes.SceneError as error:
        browser._warn(str(error))
        return ''

    choices = browser.ask_save_choices(
        stamp.read_current()[0].at_version(version), upcoming,
        title='Save Next Version', ok_text='Save v%03d' % version)
    if choices is None:
        return ''

    try:
        path, note = scenes.save_next_version()
    except scenes.SceneError as error:
        browser._warn(str(error))
        return ''

    print('[Kitsu] saved %s%s' % (path, ' - %s' % note if note else ''))
    browser.carry_out(choices, stamp.read_current()[0], path)
    return path


def update_kitsu(*_args):
    '''Post a comment and status for the scene already open.'''
    from . import browser, scenes

    path = scenes.current_path()
    if not path:
        browser._warn('Save this scene first')
        return
    browser.ask_to_publish(path)


def publish_render(*_args):
    '''Send a frame out of this version's render to Kitsu.'''
    from . import browser
    return browser.ask_to_publish_render()


def set_output_path(*_args):
    """Aim Maya's render output at the open scene's version."""
    from . import browser, scenesync, stamp

    entity_context, _source = stamp.read_current()
    if entity_context is None:
        browser._warn('No Kitsu context on this scene')
        return ''

    note = scenesync.set_output(entity_context)
    if not note:
        note = 'the output path is already correct'
    print('[Kitsu] %s' % note)
    return note


def apply_frame_range(*_args):
    '''Take Kitsu's range, rate and resolution for the open scene.'''
    from . import browser, scenesync, stamp

    entity_context, _source = stamp.read_current()
    if entity_context is None:
        browser._warn('No Kitsu context on this scene')
        return []

    wanted = scenesync.kitsu_settings(entity_context, refresh=True)
    if not wanted:
        browser._warn('Kitsu has no frame data for this one')
        return []

    changed = scenesync.apply(wanted)
    print('[Kitsu] %s' % ('applied %s' % ', '.join(changed) if changed
                          else 'already matches Kitsu'))
    return changed


def open_work_folder(*_args):
    '''Show the folder this version lives in.'''
    import subprocess
    import sys

    from BB_core import workfiles

    from . import browser, session, stamp

    entity_context, _source = stamp.read_current()
    if entity_context is None:
        browser._warn('No Kitsu context on this scene')
        return ''

    try:
        config = session.config_for(entity_context)
        # The scene folder once there is one; the task folder until then.
        folder = workfiles.scene_dir(entity_context, session.DCC, config)
        if not folder.is_dir():
            folder = workfiles.work_dir(entity_context, config)
        folder = str(folder)
    except Exception as error:
        browser._warn('No work folder for this one (%s)' % error)
        return ''

    if not os.path.isdir(folder):
        browser._warn('That folder does not exist yet:' + chr(10) * 2 + folder)
        return ''

    try:
        if sys.platform.startswith('win'):
            os.startfile(folder)                           # noqa: S606
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', folder])
        else:
            subprocess.Popen(['xdg-open', folder])
    except Exception as error:
        browser._warn('Could not open %s (%s)' % (folder, error))
    return folder
