# BB Kitsu Pipeline for JOS Compositor: this folder is a JOS plugin.
#
# JOS runs a plugin's init.py and, with the app's UI, its menu.py, with the
# plugin folder on sys.path. Point JOS at it with JOS_PLUGIN_PATH, or run
# jos/install.py to put a small loader in the user's plugin folder.
try:
    import BB_pipeline_jos
    BB_pipeline_jos.install_menu()
except Exception as _error:
    print('BB Kitsu Pipeline failed to load: %s' % _error)
