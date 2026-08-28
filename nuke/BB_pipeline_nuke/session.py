'''Session state for the Nuke integration.

Narrower than Blender's on purpose. Nuke is a compositing application, so it
browses **shots only** - an asset has no comp - and offers **compositing
tasks only**, which comes off the department list in the config exactly as
Blender's 3D filter does.

Everything cached here is lost when Nuke closes, which is correct: a shot list
from yesterday is worse than no list.
'''
from BB_core import credentials, settings, versioning, workfiles
from BB_core.config import Config
from BB_core.context import EntityContext
from BB_core.kitsu import AuthError, KitsuClient, KitsuError

DCC = 'nuke'

# Nuke browses shots. Assets are Blender's business.
ENTITY_TYPE = 'shot'


class Session:
    def __init__(self):
        self.reset()

    def reset(self):
        self.client = None
        self.user = None

        self.projects = []
        self.sequences = []
        self.shots = []
        self.tasks = []
        self.task_types = {}
        self.departments = {}
        self.statuses = []
        self.task_departments = None

        self.workfiles = []
        self.context = None

        self.message = ''
        self.is_error = False

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

    def task(self, task_id):
        return next((t for t in self.tasks if t['id'] == task_id), None)

    def task_type_name(self, task_type_id):
        task_type = self.task_types.get(task_type_id)
        return task_type['name'] if task_type else ''

    def department_of(self, task_type_id):
        task_type = self.task_types.get(task_type_id) or {}
        department = self.departments.get(task_type.get('department_id'))
        return department['name'] if department else ''

    def authors(self, task):
        '''True when Nuke is the application that *makes* this task.

        The configured departments say what a comper works in, not what a
        comper may look at. Lighting is not Nuke's to author - there is no
        script to open - but its renders are exactly what a comp reads, so
        the task still belongs in the tree.
        '''
        allowed = self.task_departments
        if allowed is None:
            return True
        return self.department_of(task.get('task_type_id', '')).lower() in allowed

    def browsable_tasks(self):
        '''Every task on the shot, authored here or not.

        Filtering these down to compositing was a mistake: it hid the
        renders a comp is assembled from behind a department the comper does
        not own.
        '''
        found = [task for task in self.tasks
                 if self.task_type_name(task.get('task_type_id', ''))]
        return sorted(found, key=lambda t: (
            not self.authors(t),
            self.task_type_name(t.get('task_type_id', '')).lower()))

    def comp_tasks(self):
        '''The shot's tasks Nuke authors, for anything that must write.'''
        return [task for task in self.browsable_tasks() if self.authors(task)]

    def say(self, message, error=False):
        self.is_error = error
        self.message = message
        if message:
            print('[Kitsu] %s' % message)


state = Session()


def config_for(entity_context=None, project_id=None):
    """The Config for a context, with the Kitsu project's own settings applied.

    Nuke gets its roots from three places in order: the settings file, the
    project's file_tree, then a [bb] block in the project brief. The last
    two only happen if the project is handed over, which is why nothing here
    calls settings.config() bare.
    """
    from BB_core import settings as _settings

    if project_id is None and entity_context is not None:
        project_id = entity_context.project_id

    # A script with no stamp gets its context from its path, and the path
    # carries no project - the names stopped repeating what the folders
    # already say, and the project is the root rather than a folder. The
    # browser knows which project is open, so fall back to that: a comper
    # works in one show at a time, and without this the brief is never read
    # and every root looks unset.
    if not project_id:
        project_id = _settings.get('last_project')

    return _settings.config(_project_for(project_id) if project_id else None)


def _project_for(project_id):
    """The project dict for an id, fetching it if the session has not got it.

    A Write can be made from the Nodes menu without the browser ever being
    opened, and then nothing has loaded the project list - so the brief that
    carries the roots would not be read, and every root would look unset.
    One small request, and only when the cache cannot answer.
    """
    found = state.project(project_id)
    if found is not None:
        return found

    if not state.connected:
        return None
    try:
        found = state.client.project(project_id)
    except Exception:
        return None

    if found:
        # Kept, so the next Write does not ask again.
        state.projects = list(state.projects) + [found]
    return found


def departments_for_nuke():
    '''Department names Nuke offers tasks from, lowercased, or None for all.'''
    try:
        configured = settings.config().dcc(DCC).get('departments')
    except Exception:
        return None
    if not configured:
        return None
    return {str(name).lower() for name in configured}
