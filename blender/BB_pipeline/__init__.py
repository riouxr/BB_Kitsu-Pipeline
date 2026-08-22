'''BB Kitsu Pipeline - Blender integration.

A thin host-side wrapper. Naming, paths, versioning and Kitsu access all live
in the shared BB_core package so that the Nuke, Resolve and Houdini
integrations behave identically; this add-on only supplies the Blender UI and
the calls into bpy.

The add-on registers even when the shared core cannot be found, because a
failed import at registration time makes Blender drop the extension with a
console traceback and no visible explanation. Instead the panel says what is
wrong and every operator refuses politely.
'''
from . import core

# Has to happen before the submodules that import BB_core are touched.
core.bootstrap()

from . import (autoconnect, capture, handlers, menu,  # noqa: E402,F401
               operators, prefs, properties, publish, render,
               review, reviewpanel, scenesync, session, thumbnails)

_modules = (prefs, properties, thumbnails, operators, menu, reviewpanel)


def register():
    if core.available:
        session.bind_core()

    for module in _modules:
        module.register()

    if core.available:
        handlers.register()
        render.register()
        autoconnect.register()
    else:
        print('BB Kitsu Pipeline: %s' % core.error)


def unregister():
    autoconnect.unregister()
    render.unregister()
    handlers.unregister()
    session.unregister_timer()

    for module in reversed(_modules):
        module.unregister()

    session.state.reset()
