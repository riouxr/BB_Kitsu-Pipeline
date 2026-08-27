'''Session state and background Kitsu calls.

Blender's UI is single-threaded and blocks on anything slow, so every network
call runs on a worker thread and hands its result back through a queue that a
timer drains on the main thread. Only the timer callback touches bpy data -
mutating properties from a worker is the classic way to crash Blender, and it
is exactly the bug that bit the standalone mocap app.

Everything cached here is session state, not scene data: it is deliberately
lost on Blender restart, because a cached shot list that is a day old is worse
than an empty one.
'''
import queue
import threading
import traceback

import bpy

# Populated by register() once the shared core has been located.
EntityContext = None
ShotContext = None
KitsuClient = None
AuthError = None
KitsuError = None
Config = None
workfiles_module = None
versioning_module = None
credentials_module = None


class Session:
    '''Everything the browser knows right now.'''

    def __init__(self):
        self.reset()

    def reset(self):
        self.client = None
        self.user = None

        self.projects = []
        self.sequences = []
        self.shots = []        # every shot in the project, filtered locally
        self.asset_types = []
        self.assets = []       # every asset in the project, filtered locally
        self.tasks = []        # tasks on the selected entity
        self.task_types = {}   # id -> task type dict
        self.departments = {}  # id -> department dict
        self.statuses = []

        # Department names this DCC offers tasks from, lowercased. None means
        # no filter is configured and every task is offered.
        self.task_departments = None

        self.workfiles = []    # [(version, Path)] for the current context
        self.context = None    # the last opened or created EntityContext

        # What the last render produced, for the review panel to submit, and
        # the render settings waiting to be put back when the job finishes.
        self.last_render = None
        self.render_restore = None

        self.message = ''
        self.is_error = False
        self.busy = ''

    # -- lookups ---------------------------------------------------------------

    @property
    def connected(self):
        return self.client is not None and self.client.logged_in

    def project(self, project_id):
        return next((p for p in self.projects if p['id'] == project_id), None)

    def sequence(self, sequence_id):
        return next((s for s in self.sequences if s['id'] == sequence_id), None)

    def shot(self, shot_id):
        return next((s for s in self.shots if s['id'] == shot_id), None)

    def shots_in(self, sequence_id):
        return [s for s in self.shots if s.get('parent_id') == sequence_id]

    def asset_type(self, asset_type_id):
        return next((a for a in self.asset_types if a['id'] == asset_type_id), None)

    def asset(self, asset_id):
        return next((a for a in self.assets if a['id'] == asset_id), None)

    def assets_of(self, asset_type_id):
        return [a for a in self.assets if a.get('entity_type_id') == asset_type_id]

    def group(self, group_id, is_asset):
        '''The sequence or the asset type, whichever tab is showing.'''
        return self.asset_type(group_id) if is_asset else self.sequence(group_id)

    def entity(self, entity_id, is_asset):
        '''The asset or the shot, whichever tab is showing.'''
        return self.asset(entity_id) if is_asset else self.shot(entity_id)

    def task(self, task_id):
        return next((t for t in self.tasks if t['id'] == task_id), None)

    def task_type_name(self, task_type_id):
        task_type = self.task_types.get(task_type_id)
        return task_type['name'] if task_type else ''

    def department_of(self, task_type_id):
        '''The department name a task type belongs to, or '' if it has none.'''
        task_type = self.task_types.get(task_type_id) or {}
        department = self.departments.get(task_type.get('department_id'))
        return department['name'] if department else ''

    def say(self, message, error=False):
        self.is_error = error
        self.message = message
        if message:
            print('[BB] %s' % message)


state = Session()


# -- background jobs -----------------------------------------------------------

_results = queue.Queue()
_pending = 0


def _drain():
    '''Timer callback: apply finished jobs on the main thread.'''
    global _pending

    while True:
        try:
            on_done, value, error = _results.get_nowait()
        except queue.Empty:
            break

        _pending -= 1
        try:
            on_done(value, error)
        except Exception:
            traceback.print_exc()
            state.say('callback failed - see the console', error=True)

    if _pending <= 0:
        state.busy = ''
    redraw()

    # Returning None unregisters the timer, so it only ticks while work is out.
    return 0.1 if _pending > 0 else None


def run(label, work, on_done, background=False):
    '''Run ``work()`` and call ``on_done(value, error)``.

    Inline by default. Browsing Kitsu measures 4-26 ms per call against the
    studio server and 225 ms for the login, which is well under the threshold
    where threading buys anything - and running inline means a popup dialog
    shows the new data on the same redraw, instead of needing a timer to
    reach into a region it cannot tag.

    ``background=True`` is for the genuinely slow work: a preview upload can
    take the better part of an hour, and that must not freeze Blender.

    ``on_done`` always runs, with ``error`` set to the exception when the job
    raised - a silent failure in a worker is invisible, and the browser needs
    to be able to say what went wrong.
    '''
    global _pending

    state.busy = label
    state.say('')

    if not background:
        try:
            value, error = work(), None
        except Exception as exception:
            traceback.print_exc()
            value, error = None, exception
        state.busy = ''
        on_done(value, error)
        return

    _pending += 1

    def target():
        try:
            _results.put((on_done, work(), None))
        except Exception as exception:
            traceback.print_exc()
            _results.put((on_done, None, exception))

    threading.Thread(target=target, daemon=True, name='bb-%s' % label).start()

    if not bpy.app.timers.is_registered(_drain):
        bpy.app.timers.register(_drain)


def busy():
    return bool(state.busy)


def redraw():
    '''Repaint the panels showing session state.

    Includes the image editor, because the render window's review panel is
    where an upload's progress is watched from.
    '''
    manager = getattr(bpy.context, 'window_manager', None)
    if not manager:
        return
    for window in manager.windows:
        for area in window.screen.areas:
            if area.type in {'VIEW_3D', 'PROPERTIES', 'IMAGE_EDITOR'}:
                area.tag_redraw()


# -- wiring --------------------------------------------------------------------

def bind_core():
    '''Import the shared core into this module's namespace.

    Done once at registration rather than at module import, so the add-on can
    register and report a clear error when the core is missing instead of
    failing to load at all.
    '''
    global EntityContext, ShotContext, KitsuClient, AuthError, KitsuError, Config
    global workfiles_module, versioning_module, credentials_module

    from .BB_core import credentials, versioning, workfiles
    from .BB_core.config import Config as _Config
    from .BB_core.context import EntityContext as _EntityContext
    from .BB_core.kitsu import AuthError as _AuthError
    from .BB_core.kitsu import KitsuClient as _KitsuClient
    from .BB_core.kitsu import KitsuError as _KitsuError

    EntityContext = _EntityContext
    ShotContext = _EntityContext
    KitsuClient = _KitsuClient
    AuthError = _AuthError
    KitsuError = _KitsuError
    Config = _Config
    workfiles_module = workfiles
    versioning_module = versioning
    credentials_module = credentials


def unregister_timer():
    if bpy.app.timers.is_registered(_drain):
        bpy.app.timers.unregister(_drain)
