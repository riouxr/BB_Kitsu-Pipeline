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

    def comp_tasks(self):
        '''The shot's tasks that belong to Nuke.

        The filter is the configured department list, the same mechanism
        Blender uses for its 3D tasks - so moving Edit into compositing, or
        splitting 2D out, is a config change and not a code change.
        '''
        allowed = self.task_departments
        found = []
        for task in self.tasks:
            task_type_id = task.get('task_type_id', '')
            name = self.task_type_name(task_type_id)
            if not name:
                continue
            if allowed is not None:
                if self.department_of(task_type_id).lower() not in allowed:
                    continue
            found.append(task)
        return sorted(found, key=lambda t: self.task_type_name(
            t.get('task_type_id', '')).lower())

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
    return _settings.config(state.project(project_id) if project_id else None)


def departments_for_nuke():
    '''Department names Nuke offers tasks from, lowercased, or None for all.'''
    try:
        configured = settings.config().dcc(DCC).get('departments')
    except Exception:
        return None
    if not configured:
        return None
    return {str(name).lower() for name in configured}
