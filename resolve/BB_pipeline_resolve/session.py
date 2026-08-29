'''Session state for the Resolve integration.

Same idea as Nuke's: Resolve browses **shots only** - an asset has no timeline
to grade or comp - and offers only the tasks in ``[dcc.resolve]``, the same
department filter every other DCC in the pipeline uses.

Everything cached here is lost when Resolve closes, which is correct: a shot
list from yesterday is worse than no list.
'''
from BB_core import settings
from BB_core.kitsu import statuses_for

DCC = 'resolve'

# Resolve browses shots. Assets are Blender's business.
ENTITY_TYPE = 'shot'


class Session:
    def __init__(self):
        self.reset()

    def reset(self):
        self.client = None
        self.user = None

        self.projects = []
        self.task_types = {}
        self.departments = {}
        self.statuses = []
        self.task_departments = None

        # the confirmed shot context
        self.project = None
        self.sequence = None
        self.shot = None
        self.shot_tasks = []

    # -- lookups ---------------------------------------------------------------

    @property
    def connected(self):
        return self.client is not None and self.client.logged_in

    def task_type_name(self, task_type_id):
        task_type = self.task_types.get(task_type_id)
        return task_type['name'] if task_type else '?'

    def department_of(self, task_type_id):
        task_type = self.task_types.get(task_type_id) or {}
        department = self.departments.get(task_type.get('department_id'))
        return department['name'] if department else ''

    def authors(self, task):
        '''True when Resolve is configured to author this task's department.'''
        allowed = self.task_departments
        if allowed is None:
            return True
        return self.department_of(task.get('task_type_id', '')).lower() in allowed

    def sort_tasks(self, tasks):
        '''Any list of shot tasks, with the ones Resolve authors listed first.

        Not filtered down to Compositing/2D - a colourist publishing from
        Resolve may still need to see a Lighting task's status - only sorted
        so the tasks this DCC is configured for are the ones on top. Shared
        by the main window's Task combo and the browser's, so a task picked
        while browsing and the task the Task combo restores afterwards are
        built from the same ordering.
        '''
        found = [task for task in (tasks or [])
                 if self.task_type_name(task.get('task_type_id', '')) != '?']
        return sorted(found, key=lambda t: (
            not self.authors(t),
            self.task_type_name(t.get('task_type_id', '')).lower()))

    def browsable_tasks(self):
        '''The current shot's tasks, sorted - see :meth:`sort_tasks`.'''
        return self.sort_tasks(self.shot_tasks)

    def project_statuses(self):
        '''Only the statuses this project actually uses.

        Kitsu keeps task statuses studio-wide; the raw list is every status
        anybody has ever needed on any production. Falls back to the whole
        list when the project names none, same as Kitsu itself does.
        '''
        return statuses_for(self.project, self.statuses)

    def config(self):
        '''The BB_core Config for the current project, roots resolved.

        Reads the project's own file_tree/brief on top of the machine's
        settings, so the render root follows whatever Kitsu says about the
        show rather than whatever was last typed in by hand.
        '''
        return settings.config(self.project)

    def load_departments_filter(self):
        try:
            configured = settings.config().dcc(DCC).get('departments')
        except Exception:
            configured = None
        self.task_departments = ({str(name).lower() for name in configured}
                                 if configured else None)

    def say(self, message, error=False):
        print('[Kitsu] %s' % message)


state = Session()
