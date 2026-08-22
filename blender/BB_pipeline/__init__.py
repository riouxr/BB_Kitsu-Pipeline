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


def _teardown(step):
    """Run one unregister step, reporting rather than aborting the rest.

    Unregistering is not all-or-nothing here: ``menu`` puts Blender's own top
    bar draw method back, and it is unregistered late. If an earlier step
    raises, that restore never happens and the top bar keeps calling into a
    module that is on its way out - a broken UI that survives until Blender
    restarts. Every step is worth attempting even when a previous one failed.
    """
    try:
        step()
    except Exception as error:                      # noqa: BLE001
        print('BB Kitsu Pipeline: %s.%s failed on unregister (%s)'
              % (getattr(step, '__module__', '?'),
                 getattr(step, '__name__', '?'), error))


def unregister():
    for step in (autoconnect.unregister, render.unregister,
                 handlers.unregister, session.unregister_timer):
        _teardown(step)

    for module in reversed(_modules):
        _teardown(module.unregister)

    session.state.reset()
