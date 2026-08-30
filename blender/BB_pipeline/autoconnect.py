'''Connecting to Kitsu on startup.

Nobody wants to press Connect every time Blender opens, and there is nothing
to ask for once the password is in the Credential Manager - so the add-on
signs in by itself and the menu loses the entry entirely.

Deferred by a timer rather than run during register(): add-on registration
happens while Blender is still starting, the preferences may not be readable
yet, and a login round trip there would be a visible pause on every launch.
The connect itself then runs on a worker thread for the same reason.

It stays quiet when it cannot help - no server configured, no stored password,
server unreachable. Connect is still on the menu whenever the session is not
live, so a failed automatic attempt is never a dead end.
'''
import bpy

DELAY = 1.0


def connect_now(context=None, background=True):
    '''Sign in with whatever is stored, exactly as the Connect menu item does.

    Used both for the startup timer below and by ``handlers.on_load`` right
    after opening a file that was never connected yet - a Launch-opened
    Blender has no reason to wait a second background thread out before it
    can restore the browser's selection to match the file it just opened.
    '''
    from . import fetch, prefs, session

    if session.state.connected:
        return False

    preferences = prefs.get(context)
    if preferences is None or not preferences.server or not preferences.email:
        return False

    if session.credentials_module is None:
        return False

    password = session.credentials_module.get_password(preferences.email)
    if not password:
        # Nothing stored, so there is nothing to connect with silently.
        return False

    print('BB Kitsu Pipeline: connecting to %s as %s'
          % (preferences.server, preferences.email))
    fetch.connect(context or bpy.context, password, background=background)
    return True


def _try_connect():
    connect_now(background=True)
    return None


def register():
    if not bpy.app.timers.is_registered(_try_connect):
        bpy.app.timers.register(_try_connect, first_interval=DELAY)


def unregister():
    if bpy.app.timers.is_registered(_try_connect):
        bpy.app.timers.unregister(_try_connect)
