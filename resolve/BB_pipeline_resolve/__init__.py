'''BB Kitsu Pipeline - DaVinci Resolve integration.

A thin host-side wrapper, the same shape as the Blender and Nuke
integrations. Naming, paths, versioning and Kitsu access all live in the
shared BB_core package; this supplies the UI and the calls into the Resolve
Project Manager and render queue.

Two UI implementations, tried in order:

  UI-B  ui_pyside.py - real PySide widgets, built directly like the Nuke
        side's browser.py. Tried first.
  UI-A  ui_a.py - Fusion's own UIManager wrapper. Several of its property
        names looked like Qt but did not behave like it, and none of it
        could be verified without a live Resolve session - kept as a proven
        fallback in case PySide is not importable in this Resolve's
        embedded Python.

Resolve is launched from Workspace > Scripts as a standalone script rather
than an installed menu, so there is no install_menu() here - the launcher
placed in Fusion's Scripts/Comp folder (resolve/kitsu_resolve_publisher.py)
bootstraps this package and calls run() directly.
'''
from . import core

# Has to happen before the submodules that import BB_core are touched.
core.bootstrap()

__version__ = '1.0.0'


def run():
    if not core.available:
        print('[kitsu] %s' % core.error)
        return None

    from . import ui_pyside
    if ui_pyside.available:
        print('[kitsu] using UI-B (PySide)')
        return ui_pyside.main()

    print('[kitsu] UI-B unavailable (%s) - falling back to UI-A (Fusion UIManager)'
         % ui_pyside.import_error)
    from . import ui_a
    return ui_a.main()
