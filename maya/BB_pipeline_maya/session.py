'''Session state for the Maya integration.

Shaped like Nuke's - one process-wide cache, thrown away when Maya closes,
because a shot list from yesterday is worse than no list - but it browses
**both shots and assets**, the way the Blender add-on does. Maya models,
rigs, animates and lights, and half of that work is assets.

Which tasks it *authors* is the department list in the config, the same
mechanism the other two hosts use. Tasks it does not author still appear in
the tree: a lighting artist has every reason to look at the rigging task's
files even though there is nothing there for Maya to open.
'''
from BB_core import settings

DCC = 'maya'

SHOT = 'shot'
ASSET = 'asset'


class Session:
    def __init__(self):
        self.reset()

    def reset(self):
        self.client = None
        self.user = None

        self.projects = []
        self.sequences = []
        self.shots = []
        self.asset_types = []
        self.assets = []
        self.tasks = []
        self.task_types = {}
        self.departments = {}
        self.statuses = []
        self.task_departments = None

        self.workfiles = []
        self.context = None

        self.message = ''
        self.is_error = False

    # -- lookups --------------------------------------------------------------

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
        return next((a for a in self.asset_types
                     if a['id'] == asset_type_id), None)

    def asset(self, asset_id):
        return next((a for a in self.assets if a['id'] == asset_id), None)

    def assets_of(self, asset_type_id):
        return [a for a in self.assets
                if a.get('entity_type_id') == asset_type_id]

    def group(self, group_id, is_asset):
        '''The asset type or the sequence, whichever branch this is.'''
        return self.asset_type(group_id) if is_asset else self.sequence(group_id)

    def entity(self, entity_id, is_asset):
        '''The asset or the shot, whichever branch this is.'''
        return self.asset(entity_id) if is_asset else self.shot(entity_id)

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
        '''True when Maya is the application that *makes* this task.

        The configured departments say what a Maya artist works in, not what
        a Maya artist may look at. A comp is not Maya's to author - there is
        no scene to open - but its output is worth seeing from here, so the
        task still belongs in the tree.
        '''
        allowed = self.task_departments
        if allowed is None:
            return True
        return self.department_of(task.get('task_type_id', '')).lower() in allowed

    def browsable_tasks(self):
        '''Every task on the entity, authored here or not.

        Nuke learned this the hard way: filtering the tree down to the
        department the artist owns hides the very renders their own work is
        assembled from. Ours first, then the rest, each alphabetically.
        '''
        found = [task for task in self.tasks
                 if self.task_type_name(task.get('task_type_id', ''))]
        return sorted(found, key=lambda t: (
            not self.authors(t),
            self.task_type_name(t.get('task_type_id', '')).lower()))

    def authored_tasks(self):
        '''The tasks Maya authors, for anything that has to write a scene.'''
        return [task for task in self.browsable_tasks() if self.authors(task)]

    def say(self, message, error=False):
        self.is_error = error
        self.message = message
        if message:
            print('[Kitsu] %s' % message)


state = Session()


def config_for(entity_context=None, project_id=None):
    '''The Config for a context, with the Kitsu project's own settings applied.

    Maya gets its roots from three places in order: the settings file, the
    project's file_tree, then a ``[bb]`` block in the project brief. The last
    two only happen if the project is found, which is what ``project_of`` is
    for - and why nothing here calls ``settings.config()`` bare.
    '''
    return settings.config(project_of(entity_context, project_id))


def project_of(entity_context=None, project_id=None):
    '''The project a context belongs to, however little it can say about it.

    Two candidates, each tried live and then from the cache before the next
    is considered: the project the context names, then the one the browser is
    pointed at. Never a third - a lookup that answers with some other show's
    brief resolves to roots that are simply wrong, and nothing on screen says
    so.

    An id that is present but resolves to nothing is worth no more than no id
    at all. Treating it as authoritative is how an artist who plainly has a
    project open gets told there is none.
    '''
    from BB_core import projects

    if project_id is None and entity_context is not None:
        project_id = entity_context.project_id

    if project_id:
        found = _project_for(project_id) or projects.cached(project_id)
        if found is not None:
            return found

    remembered = settings.get('last_project')
    if remembered and remembered != project_id:
        return _project_for(remembered) or projects.cached(remembered)
    return None


def _project_for(project_id):
    '''The project dict for an id, fetching it if the session has not got it.

    A scene can be opened from disk without the browser ever being shown, and
    then nothing has loaded the project list - so the brief that carries the
    roots would not be read, and every root would look unset. One small
    request, and only when the cache cannot answer.
    '''
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
        # Kept, so the next scene does not ask again.
        state.projects = list(state.projects) + [found]
    return found


def departments_for_maya():
    '''Department names Maya offers tasks from, lowercased, or None for all.'''
    try:
        configured = settings.config().dcc(DCC).get('departments')
    except Exception:
        return None
    if not configured:
        return None
    return {str(name).lower() for name in configured}
