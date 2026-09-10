'''Startup hook for the BB Kitsu Pipeline Maya integration.

Maya runs every ``userSetup.py`` it finds on MAYA_SCRIPT_PATH, so this coming
along with the module file is enough - there is nothing to add to an existing
one, and several may coexist.

The menu is built **deferred**. At the moment this file runs Maya has an
interpreter but no main window, so building a menu here would either fail or
silently attach to nothing; ``executeDeferred`` waits until the UI is up.
Under mayapy there is no UI at all and ``install_menu`` returns None, which is
correct rather than an error.

Nothing here raises. A broken add-on must not be the reason Maya will not
start - the traceback is printed instead, which is where a TD would look for
it anyway.
'''


def _install():
    try:
        import BB_pipeline_maya
        BB_pipeline_maya.install_menu()
    except Exception:
        import traceback
        print('BB Kitsu Pipeline: could not install the Maya menu')
        traceback.print_exc()


try:
    import maya.utils
    maya.utils.executeDeferred(_install)
except Exception:
    import traceback
    print('BB Kitsu Pipeline: could not schedule the Maya menu')
    traceback.print_exc()
