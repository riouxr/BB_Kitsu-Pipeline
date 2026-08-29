'''Turning selections into Kitsu calls and an entity context.

Everything that talks to the server or the filesystem on the browser's behalf
lives here, so the operators stay thin and the property callbacks stay free of
network code.

Shots and assets go through the same functions throughout. The only places
that care which tree is showing are the two Kitsu routes - a shot's tasks and
an asset's tasks - and the path template the core picks off the context.
'''
import traceback

from . import prefs, properties, session, thumbnails

DCC = 'blender'


def _state():
    return session.state


# -- context -------------------------------------------------------------------

def current_context(context=None):
    '''An EntityContext for the current selection, or None if incomplete.

    The project *code* is preferred over its name: codes are what the studio
    naming scheme uses (VIL, not "Villa Project"), and a code that is missing
    on a test project falls back to the name rather than producing a nameless
    path.
    '''
    state = _state()
    props = properties.get(context)
    is_asset = props.is_asset

    project = state.project(props.project)
    group = state.group(props.group_id, is_asset)
    entity = state.entity(props.entity_id, is_asset)
    task = state.task(props.task)

    if not (project and group and entity and task):
        return None

    task_type_id = task.get('task_type_id', '')
    return session.EntityContext(
        entity_type='asset' if is_asset else 'shot',
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


# -- login ---------------------------------------------------------------------

def _task_departments(context=None):
    '''The department names this DCC offers tasks from, or None for all.'''
    try:
        settings = prefs.config(context).dcc(DCC)
    except Exception:
        return None
    departments = settings.get('departments')
    if not departments:
        return None
    return {str(name).lower() for name in departments}


def connect(context, password, background=False):
    '''Log in, then pull the project list.

    ``background=True`` is for the automatic connect at startup, so Blender
    does not sit still for the login round trip while it is opening.
    '''
    state = _state()
    preferences = prefs.get(context)

    client = session.KitsuClient(preferences.server,
                                 verify=not preferences.allow_insecure_tls)

    def work():
        client.log_in(preferences.email, password)
        return client.open_projects()

    def done(projects, error):
        if error:
            from .BB_core.kitsu import explain

            state.client = None
            state.say(explain(error, preferences.server, preferences.email),
                      error=True)
            return

        state.client = client
        state.user = client.user
        state.projects = projects or []
        state.sequences = []
        state.shots = []
        state.asset_types = []
        state.assets = []
        state.tasks = []
        state.task_departments = _task_departments(context)
        properties.clear_workfiles(context)

        if preferences.remember_password and session.credentials_module.available():
            session.credentials_module.set_password(preferences.email, password)

        name = (client.user or {}).get('full_name') or preferences.email
        state.say('connected as %s - %d project(s)' % (name, len(state.projects)))

        # Pick up where the browser was left. Setting the project runs the
        # usual cascade, which restores the rest.
        remembered = prefs.recall(context)
        if any(p['id'] == remembered['project'] for p in state.projects):
            props = properties.get(context)
            props.project = remembered['project']

    session.run('connecting', work, done, background=background)


def disconnect(context):
    state = _state()
    if state.client:
        state.client.log_out()
    state.reset()
    properties.clear_workfiles(context)
    state.say('disconnected')


# -- cascading fetches ---------------------------------------------------------

def project_selected(context):
    '''Pull both trees, the task types, departments and statuses for a project.

    All of it in one job so that narrowing afterwards is a local filter. Task
    types, departments and statuses are global in Kitsu but are fetched here
    rather than at login, because they are only needed once a project is in
    play - the departments drive the per-DCC task filter, and the statuses
    fill the dropdown on the publish dialog.
    '''
    state = _state()
    props = properties.get(context)
    project_id = props.project

    state.sequences = []
    state.shots = []
    state.asset_types = []
    state.assets = []
    state.tasks = []
    # Suspended: these are resets on the way to loading a project, not
    # choices. Letting them fire the cascade would refetch nothing useful and
    # overwrite the bookmark being restored a moment later.
    with properties.suspend_updates():
        properties.clear(props, 'sequence', 'shot', 'asset_type', 'asset', 'task')
    properties.clear_workfiles(context)

    if not state.connected or not properties._valid(project_id):
        return

    client = state.client

    def work():
        return (client.sequences(project_id),
                client.shots_for_project(project_id),
                client.asset_types(project_id),
                client.assets_for_project(project_id),
                client.task_types(),
                client.departments(),
                client.task_statuses())

    def done(result, error):
        if error:
            state.say(str(error), error=True)
            return

        (sequences, shots, asset_types, assets,
         task_types, departments, statuses) = result
        state.sequences = sequences
        state.shots = shots
        state.asset_types = asset_types
        state.assets = assets
        state.task_types = {t['id']: t for t in task_types}
        state.departments = {d['id']: d for d in departments}
        # Scoped to this show: the raw list is every status any
        # production has ever needed.
        from .BB_core.kitsu import statuses_for
        state.statuses = statuses_for(state.project(project_id), statuses)

        # Kept so a scene opened later, in a Blender that has not connected,
        # can still find the roots the project's brief carries.
        from .BB_core import projects
        projects.remember(state.project(project_id))
        state.task_departments = _task_departments(context)

        state.say('%d shot(s), %d asset(s)' % (len(shots), len(assets)))
        _restore_remembered(context)

    session.run('loading project', work, done)


def _restore_remembered(context):
    '''Put the browser back where it was, as far as Kitsu still allows.

    Called after a project's data lands. Anything that has since been deleted
    simply will not be found, and the selector falls back to its first entry -
    which is the right outcome, and the reason each level is checked against
    what actually came back rather than assumed.
    '''
    state = _state()
    props = properties.get(context)
    remembered = prefs.recall(context)

    known = ({s['id'] for s in state.sequences} | {s['id'] for s in state.shots}
             | {a['id'] for a in state.asset_types}
             | {a['id'] for a in state.assets})

    with properties.suspend_updates():
        if remembered['entity_type'] in ('SHOT', 'ASSET'):
            props.entity_type = remembered['entity_type']
        for name in ('sequence', 'shot', 'asset_type', 'asset'):
            if remembered[name] and remembered[name] in known:
                setattr(props, name, remembered[name])

    entity_selected(context)

    if remembered['task'] and any(t['id'] == remembered['task']
                                  for t in state.tasks):
        with properties.suspend_updates():
            props.task = remembered['task']
        refresh_workfiles(context)


def refresh_project(context=None):
    """Re-read the project from Kitsu, keeping the current selection.

    The session caches both trees so that narrowing is a local filter, but a
    cache is only right until somebody adds a sequence in Kitsu - and they do,
    constantly. The browser calls this every time it opens, which costs a
    handful of requests measured in tens of milliseconds and means the lists
    are never stale.

    Selections are restored by id afterwards, so a refresh does not throw away
    where you were. Anything that has since been deleted in Kitsu simply
    cannot be restored, which is the correct outcome.
    """
    state = _state()
    props = properties.get(context)

    if not state.connected or not properties._valid(props.project):
        return

    project_id = props.project
    client = state.client
    keep = {
        'entity_type': props.entity_type,
        'sequence': props.sequence,
        'shot': props.shot,
        'asset_type': props.asset_type,
        'asset': props.asset,
        'task': props.task,
    }

    def work():
        return (client.sequences(project_id),
                client.shots_for_project(project_id),
                client.asset_types(project_id),
                client.assets_for_project(project_id),
                client.task_types(),
                client.departments(),
                client.task_statuses())

    def done(result, error):
        if error:
            state.say(str(error), error=True)
            return

        (sequences, shots, asset_types, assets,
         task_types, departments, statuses) = result
        state.sequences = sequences
        state.shots = shots
        state.asset_types = asset_types
        state.assets = assets
        state.task_types = {t['id']: t for t in task_types}
        state.departments = {d['id']: d for d in departments}
        # Scoped to this show: the raw list is every status any
        # production has ever needed.
        from .BB_core.kitsu import statuses_for
        state.statuses = statuses_for(state.project(project_id), statuses)

        # Kept so a scene opened later, in a Blender that has not connected,
        # can still find the roots the project's brief carries.
        from .BB_core import projects
        projects.remember(state.project(project_id))
        state.task_departments = _task_departments(context)

        known = ({s['id'] for s in sequences} | {s['id'] for s in shots}
                 | {a['id'] for a in asset_types} | {a['id'] for a in assets})

        with properties.suspend_updates():
            props.entity_type = keep['entity_type']
            for name in ('sequence', 'shot', 'asset_type', 'asset'):
                if keep[name] in known:
                    setattr(props, name, keep[name])

        # Tasks hang off the entity, so they are refetched rather than kept.
        entity_selected(context)
        if keep['task'] in {t['id'] for t in state.tasks}:
            with properties.suspend_updates():
                props.task = keep['task']
            refresh_workfiles(context)

        state.say('%d shot(s), %d asset(s)' % (len(shots), len(assets)))

    session.run('refreshing', work, done)


def entity_selected(context):
    '''Pull the tasks for the selected shot or asset.'''
    state = _state()
    props = properties.get(context)
    is_asset = props.is_asset
    entity_id = props.entity_id

    state.tasks = []
    with properties.suspend_updates():
        properties.clear(props, 'task')
    properties.clear_workfiles(context)

    if not state.connected or not properties._valid(entity_id):
        return

    client = state.client

    # Pulled here rather than in draw(): a draw callback runs on every redraw
    # and must never touch the network.
    thumbnails.fetch(client, state.entity(entity_id, is_asset))

    def work():
        return (client.tasks_for_asset(entity_id) if is_asset
                else client.tasks_for_shot(entity_id))

    def done(tasks, error):
        if error:
            state.say(str(error), error=True)
            return

        state.tasks = tasks or []
        if not state.tasks:
            state.say('nothing to do here - no tasks in Kitsu', error=True)
            return

        # The filter is what the browser will actually show, so report on that
        # rather than on the raw count: "3 tasks" next to an empty dropdown is
        # a worse answer than saying none of them belong to this application.
        offered = properties.task_items(props, context)
        if not properties._valid(offered[0][0]):
            state.say('no 3D tasks here - %d task(s) belong to other departments'
                      % len(state.tasks), error=True)
            return

        state.say('%d task(s)' % len(offered))
        refresh_workfiles(context)

    session.run('loading tasks', work, done)


# -- work files ----------------------------------------------------------------

def refresh_workfiles(context=None):
    '''Rescan the work folder for versions of the current context.

    Leaves the version selector pointing at the file that is already open
    when there is one, so "the version I am in" is the default rather than
    "the newest that exists".
    '''
    import bpy

    state = _state()
    props = properties.get(context)

    entity_context = current_context(context)
    if entity_context is None:
        properties.clear_workfiles(context)
        return

    try:
        config = prefs.config(context)
        found = session.workfiles_module.list_workfiles(entity_context, DCC, config)
    except session.workfiles_module.RootNotConfigured as error:
        properties.clear_workfiles(context)
        state.say(str(error), error=True)
        return
    except Exception as error:
        traceback.print_exc()
        properties.clear_workfiles(context)
        state.say('cannot read the work folder: %s' % error, error=True)
        return

    state.workfiles = found
    if not found:
        properties.clear(props, 'version')
        state.say('no scene file yet for this task')
        return

    open_file = (bpy.data.filepath or '').lower()
    current = next((version for version, path in found
                    if str(path).lower() == open_file), None)
    props.version = str(current if current is not None else found[-1][0])
    # Nothing to announce: the version dropdown next to this already says how
    # many there are, and the line only pushed real messages off the panel.
    state.say('')
