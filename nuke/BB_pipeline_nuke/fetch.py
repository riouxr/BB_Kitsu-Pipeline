'''Kitsu calls for the Nuke browser.

Shots only, compositing only. The cascade is the same shape as Blender's -
project, then sequence, then shot, then task - minus the asset tree Nuke has
no use for.

Calls run inline. Measured against the studio server a shot or task list is
4-26 ms and the login 225 ms, which is far below where threading earns its
complexity, and inline means a Qt dialog shows the answer on the same repaint.
'''
from BB_core import credentials, settings, workfiles
from BB_core.kitsu import AuthError, KitsuClient, KitsuError, explain

from . import session

state = session.state


def current_context(project_id, sequence_id, shot_id, task_id):
    '''An EntityContext for the given selection, or None if incomplete.'''
    from BB_core.context import EntityContext

    project = state.project(project_id)
    sequence = state.sequence(sequence_id)
    shot = state.shot(shot_id)
    task = state.task(task_id)

    if not (project and sequence and shot and task):
        return None

    task_type_id = task.get('task_type_id', '')
    return EntityContext(
        entity_type=session.ENTITY_TYPE,
        project=project.get('code') or project.get('name', ''),
        group=sequence.get('name', ''),
        entity=shot.get('name', ''),
        task=state.task_type_name(task_type_id),
        project_id=project['id'],
        group_id=sequence['id'],
        entity_id=shot['id'],
        task_id=task['id'],
        task_type_id=task_type_id,
        department=state.department_of(task_type_id),
        version=0,
        server=state.client.host if state.client else '',
    )


def connect(password=None):
    '''Log in and pull the project list. Returns '' or a message.'''
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
    state.task_departments = session.departments_for_nuke()

    if values.get('remember_password', True):
        credentials.set_password(email, password)

    name = (client.user or {}).get('full_name') or email
    state.say('connected as %s - %d project(s)' % (name, len(state.projects)))
    return ''


def project_selected(project_id):
    '''Pull the sequences, shots, task types, departments and statuses.'''
    state.sequences = []
    state.shots = []
    state.tasks = []

    if not state.connected or not project_id:
        return

    client = state.client
    try:
        state.sequences = client.sequences(project_id)
        state.shots = client.shots_for_project(project_id)
        task_types = client.task_types()
        state.task_types = {t['id']: t for t in task_types}
        state.departments = {d['id']: d for d in client.departments()}
        state.statuses = client.task_statuses()
    except KitsuError as error:
        state.say(str(error), error=True)
        return

    state.task_departments = session.departments_for_nuke()
    state.say('%d sequence(s), %d shot(s)' % (len(state.sequences), len(state.shots)))


def shot_selected(shot_id):
    '''Pull the tasks for one shot.'''
    state.tasks = []
    if not state.connected or not shot_id:
        return

    try:
        state.tasks = state.client.tasks_for_shot(shot_id) or []
    except KitsuError as error:
        state.say(str(error), error=True)
        return

    offered = state.comp_tasks()
    if not state.tasks:
        state.say('this shot has no tasks in Kitsu', error=True)
    elif not offered:
        state.say('no compositing tasks here - %d task(s) belong to other '
                  'departments' % len(state.tasks), error=True)
    else:
        state.say('%d compositing task(s)' % len(offered))


def list_versions(entity_context):
    '''Every .nk version on disk for a context, as ``(version, Path)``.'''
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
