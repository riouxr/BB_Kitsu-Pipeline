'''Kitsu calls for the Maya browser.

The same cascade the other hosts use - project, then group, then entity, then
task - over both branches, because Maya works on shots and assets alike.

Calls run inline. Measured against the studio server a shot or task list is
4-26 ms and the login 225 ms, which is far below where threading earns its
complexity, and inline means a Qt dialog shows the answer on the same repaint.
'''
from BB_core import credentials, settings, workfiles
from BB_core.context import EntityContext
from BB_core.kitsu import AuthError, KitsuClient, KitsuError, explain

from . import session

state = session.state


def current_context(project_id, group_id, entity_id, task_id, is_asset):
    '''An EntityContext for the given selection, or None if incomplete.'''
    project = state.project(project_id)
    group = state.group(group_id, is_asset)
    entity = state.entity(entity_id, is_asset)
    task = state.task(task_id)

    if not (project and group and entity and task):
        return None

    task_type_id = task.get('task_type_id', '')
    return EntityContext(
        entity_type=session.ASSET if is_asset else session.SHOT,
        project=project.get('code') or project.get('name', ''),
        group=group.get('name', ''),
        entity=entity.get('name', ''),
        task=state.task_type_name(task_type_id),
        project_id=project['id'],
        group_id=group['id'],
        entity_id=entity['id'],
        task_id=task['id'],
        task_type_id=task_type_id,
        department=state.department_of(task_type_id),
        version=0,
        server=state.client.host if state.client else '',
    )


def connect(password=None):
    """Log in and pull the project list. Returns '' or a message."""
    values = settings.load()
    server, email = values.get('server'), values.get('email')

    if not server or not email:
        return 'Set the Kitsu server and your email in Kitsu > Settings...'

    if password is None:
        password = credentials.get_password(email)
    if not password:
        return ('No password stored for %s - type one in '
                'Kitsu > Settings...' % email)

    client = KitsuClient(server, verify=not values.get('allow_insecure_tls'))
    try:
        client.log_in(email, password)
        projects = client.open_projects()
    except (AuthError, KitsuError) as error:
        state.client = None
        message = explain(error, server, email)
        state.say(message, error=True)
        return message

    state.client = client
    state.user = client.user
    state.projects = projects or []
    state.task_departments = session.departments_for_maya()

    if values.get('remember_password', True):
        credentials.set_password(email, password)

    name = (client.user or {}).get('full_name') or email
    state.say('connected as %s - %d project(s)' % (name, len(state.projects)))
    return ''


def project_selected(project_id):
    '''Pull both trees, plus the task types, departments and statuses.'''
    state.sequences = []
    state.shots = []
    state.asset_types = []
    state.assets = []
    state.tasks = []

    if not state.connected or not project_id:
        return

    # Kept before anything else, so a scene opened later can find the roots
    # with no session at all.
    from BB_core import projects
    projects.remember(state.project(project_id))

    client = state.client
    try:
        state.sequences = client.sequences(project_id)
        state.shots = client.shots_for_project(project_id)
        state.asset_types = client.asset_types(project_id)
        state.assets = client.assets_for_project(project_id)
        task_types = client.task_types()
        state.task_types = {t['id']: t for t in task_types}
        state.departments = {d['id']: d for d in client.departments()}
        # Scoped to this show: Kitsu keeps statuses studio-wide and each
        # project picks the ones it uses.
        from BB_core.kitsu import statuses_for
        state.statuses = statuses_for(state.project(project_id),
                                      client.task_statuses())
    except KitsuError as error:
        state.say(str(error), error=True)
        return

    state.task_departments = session.departments_for_maya()
    state.say('%d shot(s), %d asset(s)'
              % (len(state.shots), len(state.assets)))


def entity_selected(entity_id, is_asset):
    '''Pull the tasks for one shot or asset.'''
    state.tasks = []
    if not state.connected or not entity_id:
        return

    try:
        if is_asset:
            state.tasks = state.client.tasks_for_asset(entity_id) or []
        else:
            state.tasks = state.client.tasks_for_shot(entity_id) or []
    except KitsuError as error:
        state.say(str(error), error=True)
        return

    offered = state.authored_tasks()
    if not state.tasks:
        state.say('this %s has no tasks in Kitsu'
                  % ('asset' if is_asset else 'shot'), error=True)
    elif not offered:
        state.say('nothing here for Maya to author - %d task(s) belong to '
                  'other departments' % len(state.tasks))
    else:
        state.say('%d task(s) Maya authors' % len(offered))


def list_versions(entity_context):
    '''Every Maya version on disk for a context, as ``(version, Path)``.'''
    if entity_context is None:
        return []
    try:
        found = workfiles.list_workfiles(entity_context, session.DCC,
                                         session.config_for(entity_context))
    except Exception as error:
        state.say(str(error), error=True)
        return []
    state.workfiles = found
    return found
