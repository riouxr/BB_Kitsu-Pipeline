'''Rendering the current timeline and publishing it to Kitsu.

Deliberately two separate steps, ``render`` and ``upload`` - so the UI can
put a review in between them: render once, scrub the result inside Resolve,
then publish that exact file, rather than one action that renders and
uploads in the same breath with no chance to catch a bad take first.

The render folder comes from :mod:`BB_core.workfiles`, the same module the
Blender and Nuke sides build their render paths from, resolved from the
Kitsu project's own settings - never typed in by hand.

Publishing goes through the shared ``KitsuClient.publish_preview``, so a
Resolve export lands on a task identically to a Blender or Nuke publish.
'''
import os

from BB_core import settings, workfiles
from BB_core.context import EntityContext
from BB_core.versioning import tag_comment

from . import resolve_ops, session

state = session.state

# The review stream: a single movie file with burn-ins, at delivery
# colourspace - what Resolve exports, and the same stream the "offline"
# entry in the config already describes for every other DCC.
STREAM = 'offline'

# The stream another DCC's render lives on - EXR frames with no folder
# prefix of its own, the same "main" stream Blender and Nuke publish to.
RENDER_STREAM = 'main'


def build_context(task, version, project=None, sequence=None, shot=None):
    '''An :class:`EntityContext` for a shot/task.

    Defaults to the shot/task currently assigned (what Render/Publish use);
    the Browse tab passes its own ``project``/``sequence``/``shot`` to build
    one for a task that has not been opened at all - only browsed - which is
    what looking for another department's renders needs.
    '''
    project  = project  if project  is not None else (state.project or {})
    sequence = sequence if sequence is not None else (state.sequence or {})
    shot     = shot     if shot     is not None else (state.shot or {})
    task_type_id = (task or {}).get('task_type_id', '')

    return EntityContext(
        entity_type='shot',
        project=project.get('name', ''),
        group=sequence.get('name', ''),
        entity=shot.get('name', ''),
        task=state.task_type_name(task_type_id),

        project_id=project.get('id', ''),
        group_id=sequence.get('id', ''),
        entity_id=shot.get('id', ''),
        task_id=(task or {}).get('id', ''),
        task_type_id=task_type_id,
        department=state.department_of(task_type_id),

        version=version,
        server=state.client.host if state.client else '',
    )


def render_target(task, version):
    '''``(folder, file_stem)`` for this shot's review render.

    The root is resolved from the Kitsu project's own settings - its
    file_tree or Brief - falling back to this machine's configured render
    root, exactly as :mod:`BB_core.workfiles` resolves it for every other
    DCC. Raises :class:`BB_core.workfiles.RootNotConfigured` when nothing
    names one, which the caller surfaces rather than guessing a path.
    '''
    context = build_context(task, version)
    config = state.config()
    folder = workfiles.render_dir(context, STREAM, config)
    stem = workfiles.render_stem(context, config)
    return str(folder), stem


def render(project, task, version, preset, log=print, on_progress=None):
    '''Render the open timeline to this shot/task's review path.

    Returns the rendered file's path. Nothing here talks to Kitsu - the
    file sits on disk until ``upload`` sends it, so a render can be scrubbed
    in Resolve, and even abandoned, without ever touching Kitsu.
    '''
    folder, stem = render_target(task, version)
    os.makedirs(folder, exist_ok=True)

    log('[render] %s / %s.mp4' % (folder, stem))
    job_id, out_file = resolve_ops.setup_render_job(project, folder, stem, preset)
    resolve_ops.wait_for_render(project, job_id, log=log, on_progress=on_progress)

    actual = None
    for candidate in (out_file, os.path.splitext(out_file)[0] + '_1.mp4'):
        if os.path.exists(candidate):
            actual = candidate
            break
    if not actual:
        for name in sorted(os.listdir(folder), reverse=True):
            if name.startswith(stem) and name.endswith('.mp4'):
                actual = os.path.join(folder, name)
                break
    if not actual:
        raise RuntimeError('Rendered file not found in: ' + folder)

    log('[render] file=%s' % actual)
    return actual


def render_versions_for(project, sequence, shot, task):
    '''Every rendered version of one task's sequence, oldest first.

    Read straight off the render root via :mod:`BB_core.workfiles` - not
    through Kitsu, which only ever holds the review movie - the same way
    Nuke's browser lists a "(renders)" task's frames without opening
    anything. Used to decide whether Import in Media Storage has anything
    to offer for the task currently browsed, so it never needs a task to
    have been opened as a Resolve project at all.

    Raises rather than swallowing - a missing render root, or one that
    resolves to a different machine's path than Blender or Nuke actually
    wrote to, has to be visible to the person looking for a sequence that is
    plainly on disk, not silently reported as "nothing found".
    '''
    context = build_context(task, 1, project, sequence, shot)
    config = settings.config(project)
    return workfiles.render_versions(context, RENDER_STREAM, config)


def master_frames_for(project, sequence, shot, task):
    '''``(pattern, first, last)`` for what is in Master right now, or None.

    Same shape as one row of ``render_versions_for``, so the browser can
    treat a Master hit exactly like any other render row once it has one.
    '''
    from BB_core import master as master_mod

    context = build_context(task, 1, project, sequence, shot)
    config = settings.config(project)
    return master_mod.master_frames(context, RENDER_STREAM, config)


def master_dir_for(project, sequence, shot, task):
    '''The Master folder itself, for importing straight off it.'''
    from BB_core import master as master_mod

    context = build_context(task, 1, project, sequence, shot)
    config = settings.config(project)
    return str(master_mod.master_dir(context, RENDER_STREAM, config))


def render_folder_for(project, sequence, shot, task, version):
    '''The folder holding one rendered version of a task, for importing.'''
    context = build_context(task, version, project, sequence, shot)
    config = settings.config(project)
    return str(workfiles.render_dir(context, RENDER_STREAM, config))


def upload(task, status, comment, file_path, log=print):
    '''Publish an already-rendered file against ``task``. No re-render.

    Tags the comment with the current Resolve project's version, the same
    way a Blender or Nuke publish tags its work-file version - the only
    durable link between a Kitsu preview revision and the project that
    produced it, since a colourist can publish several stills off one
    project version exactly as easily as a 3D artist can publish several
    angles off one saved scene. Untagged (silently - launching from an
    untagged image already falls back to the latest project version, the
    same as the other two DCCs) when there is no current project to read a
    version from, which should not happen in practice but costs nothing to
    guard against.
    '''
    project = resolve_ops.get_current_project()
    version = resolve_ops.version_from_name(project.GetName()) if project else None
    text = tag_comment(comment, version) if version else comment

    log('[publish] file=%s' % file_path)
    return state.client.publish_preview(
        task['id'], file_path, comment=text,
        task_status_id=status['id'] if status else None,
        normalize=False, log=log)
