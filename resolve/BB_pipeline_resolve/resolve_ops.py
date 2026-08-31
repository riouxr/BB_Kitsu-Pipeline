'''Talking to the Resolve Project Manager and render queue.

Nothing Kitsu-specific lives here - just the DaVinci Resolve API calls and the
project-naming scheme that makes a Resolve project (a flat, studio-wide
namespace with no folders under it) collision-safe: ``proj_seq_shot_vNNN``.

That naming stays local to this module rather than going through BB_core's
generic ``{entity}`` template - the template deliberately drops the project
and sequence because a *file* already sits inside folders that say them, but a
Resolve project has no folder of its own, so it has to spell them out to stay
unique across shows.
'''
import os
import re
import tempfile
import time

from BB_core import naming
from BB_core.config import Config

_CFG = Config()

_resolve_cache = None


def _connect_external():
    '''Set up Resolve's standard external-scripting environment and connect
    via ``DaVinciResolveScript``.

    Needed because ``bmd`` only exists for a script run through Fusion's own
    script host (Workspace > Scripts, or the Fusion console) - and running
    a PySide UI with its own event loop *there* is a long-documented Fusion
    bug ("PySide freezes Fusion", reported since Fusion 7): a second Qt
    event loop conflicts with Fusion's own. The standard workaround, and
    the reason this tool now has to be launched as its own process rather
    than from Workspace > Scripts, is to run standalone and connect back in
    exactly the way this function does.
    '''
    import sys

    if os.name == 'nt':
        os.environ.setdefault(
            'RESOLVE_SCRIPT_API',
            r'C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting')
        os.environ.setdefault(
            'RESOLVE_SCRIPT_LIB',
            r'C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll')
    elif sys.platform == 'darwin':
        os.environ.setdefault(
            'RESOLVE_SCRIPT_API',
            '/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting')
        os.environ.setdefault(
            'RESOLVE_SCRIPT_LIB',
            '/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so')
    else:
        os.environ.setdefault(
            'RESOLVE_SCRIPT_API', '/opt/resolve/Developer/Scripting')
        os.environ.setdefault(
            'RESOLVE_SCRIPT_LIB', '/opt/resolve/libs/Fusion/fusionscript.so')

    modules_path = os.path.join(os.environ['RESOLVE_SCRIPT_API'], 'Modules')
    if modules_path not in sys.path:
        sys.path.append(modules_path)

    import DaVinciResolveScript as dvr
    return dvr.scriptapp('Resolve')


def get_resolve():
    '''The running Resolve instance, however this process reached it.

    Cached: ``_connect_external`` does real work (env vars, a fresh module
    import, a connection handshake) that only needs doing once per process.
    '''
    global _resolve_cache
    if _resolve_cache is not None:
        return _resolve_cache

    try:
        _resolve_cache = bmd.scriptapp('Resolve')  # noqa: F821
    except NameError:
        _resolve_cache = _connect_external()
    return _resolve_cache


def get_project_manager():
    return get_resolve().GetProjectManager()


def get_current_project():
    return get_project_manager().GetCurrentProject()


def refresh_master_clip(context, stream, config, log=print):
    '''Force Resolve to notice Master's new content, if it is already open.

    Resolve caches media by path and does not detect new bytes written under
    an unchanged one - the reason "Set as Master" alone left a stale colour
    grade against fresh frames until someone manually relinked or cleared
    the render cache by hand. ``MediaPool.RelinkClips`` against the same
    folder is the documented way to make Resolve re-read it from a script,
    the same fix a colourist would reach for, just automatic.

    Silent, not an error, when there is nothing to refresh: Resolve is not
    running, no project is open, or no clip has been imported from Master
    yet - the next Import will pick up the new frames regardless.
    '''
    try:
        resolve = get_resolve()
        project = resolve.GetProjectManager().GetCurrentProject() if resolve else None
    except Exception:
        return False
    if not project:
        return False

    media_pool = project.GetMediaPool()
    if not media_pool:
        return False

    from BB_core import master as master_mod
    folder = str(master_mod.master_dir(context, stream, config))
    root = media_pool.GetRootFolder()
    if not root:
        return False

    clip = _find_clip_in_folder(root, folder)
    if clip is None:
        return False

    ok = media_pool.RelinkClips([clip], folder)
    if ok:
        log('[master] relinked Resolve clip against %s' % folder)
    return bool(ok)


def _find_clip_in_folder(pool_folder, target_dir):
    '''The first clip anywhere in this Media Pool folder tree whose source
    file lives in ``target_dir``, or None.'''
    target = os.path.normcase(os.path.normpath(target_dir))
    for clip in pool_folder.GetClipList() or []:
        path = clip.GetClipProperty('File Path') or ''
        if os.path.normcase(os.path.normpath(os.path.dirname(path))) == target:
            return clip
    for sub in pool_folder.GetSubFolderList() or []:
        found = _find_clip_in_folder(sub, target_dir)
        if found is not None:
            return found
    return None


def import_sequence_folder(folder, log=print):
    '''Bring every frame in a folder into the Media Pool as one clip.

    Goes through Resolve's Media Storage - the filesystem browser, not the
    project's own Media Pool - which is what actually groups a folder of
    numbered frames into a single sequence clip the way picking it by hand
    in the Media page would. ``MediaPool.ImportMedia`` (what Review uses for
    a single movie file) does not do that grouping for a bare frame glob.
    '''
    resolve = get_resolve()
    storage = resolve.GetMediaStorage()
    if not storage:
        raise RuntimeError('Resolve has no Media Storage for this session.')

    imported = storage.AddItemListToMediaPool([str(folder)])
    if not imported:
        raise RuntimeError('Resolve could not import: ' + str(folder))
    log('[import] added %d item(s) from %s' % (len(imported), folder))
    return imported


def sanitize(value):
    '''A name field reduced to the pipeline's shared character set.

    The same rules ``naming.sanitize`` applies to every other DCC's names,
    so a shot called "FF9 / 0070" comes out identically here and in a Blender
    filename or a Nuke Write path.
    '''
    return naming.sanitize(value, _CFG)


def build_project_base(proj_name, seq_name, shot_name, task_name):
    '''``Project_Sequence_Shot_Task`` - one Resolve project per shot *and* task.

    A Resolve project is a flat, studio-wide namespace with no folder of its
    own, unlike a work file that already sits inside a task folder - so the
    task has to be spelled into the name here or a colourist's Color Grading
    project and a compositor's Compositing project on the same shot would
    collide on one name.
    '''
    return '_'.join(sanitize(x) for x in (proj_name, seq_name, shot_name, task_name))


def matching_versions(base, existing_names):
    '''Every ``(version, name)`` already on disk for this base, sorted.'''
    pattern = re.compile(r'^' + re.escape(base) + r'_v(\d+)$', re.IGNORECASE)
    found = []
    for name in existing_names:
        m = pattern.match(name)
        if m:
            found.append((int(m.group(1)), name))
    return sorted(found)


def next_version_name(base, existing_names):
    used = [version for version, _name in matching_versions(base, existing_names)]
    next_v = (max(used) + 1) if used else 1
    return '%s_v%03d' % (base, next_v), next_v


def latest_existing(base, existing_names):
    '''``(name, version)`` of the highest version already on disk, or None.

    What "Open" offers - the same shot+task, picked up where it was left.
    '''
    matches = matching_versions(base, existing_names)
    if not matches:
        return None
    version, name = matches[-1]
    return name, version


def version_from_name(name):
    m = re.search(r'_v(\d+)$', name, re.IGNORECASE)
    return int(m.group(1)) if m else 1


def get_all_resolve_project_names():
    try:
        raw = get_project_manager().GetProjectListInCurrentFolder()
        if isinstance(raw, dict):
            return list(raw.values())
        if isinstance(raw, (list, tuple)):
            return list(raw)
    except Exception as e:
        print('[resolve] project list error: %s' % e)
    return []


def copy_resolve_project(src_name, dst_name, log=print):
    '''Export src to a temp .drp, re-import as dst_name. Original untouched.'''
    pm = get_project_manager()
    tmp = os.path.join(tempfile.gettempdir(), '_kitsu_copy_%d.drp' % int(time.time()))
    try:
        log("[resolve] loading '%s' for export..." % src_name)
        if not pm.LoadProject(src_name):
            log("[resolve] ERROR: cannot load '%s'" % src_name)
            return False
        log('[resolve] exporting to temp .drp ...')
        if not pm.ExportProject(src_name, tmp, False):
            log('[resolve] ERROR: ExportProject failed')
            return False
        log("[resolve] importing as '%s'..." % dst_name)
        if not pm.ImportProject(tmp, dst_name):
            log('[resolve] ERROR: ImportProject failed')
            return False
        log("[resolve] opening '%s'..." % dst_name)
        if not pm.LoadProject(dst_name):
            log("[resolve] WARNING: could not auto-open '%s'" % dst_name)
        return True
    except Exception as e:
        log('[resolve] copy_project error: %s' % e)
        return False
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def setup_render_job(project, folder, file_stem, preset_name=None):
    tl = project.GetCurrentTimeline()
    if not tl:
        raise RuntimeError('No active timeline.')

    # A render job snapshots the timeline's frame range at the moment it is
    # added - it is not live-linked - so a job left over from an earlier,
    # longer cut of this same timeline can still be sitting in the queue
    # with an out-of-range frame count. Clearing the queue before adding
    # this render's job is what stops a trim from failing against a job
    # that no longer matches anything on the timeline.
    try:
        project.DeleteAllRenderJobs()
    except Exception:
        pass

    if preset_name:
        project.LoadRenderPreset(preset_name)
    else:
        project.SetCurrentRenderFormatAndCodec('mp4', 'H264')
    project.SetRenderSettings({
        'SelectAllFrames': True, 'TargetDir': folder,
        'CustomName': file_stem, 'UniqueFilenameStyle': 0,
    })
    job_id = project.AddRenderJob()
    if not job_id:
        raise RuntimeError('Failed to add render job.')
    return job_id, os.path.join(folder, file_stem + '.mp4')


def wait_for_render(project, job_id, log=print, on_progress=None):
    '''Poll a render job to completion.

    ``on_progress(percent)`` is called on every poll, in addition to
    ``log`` - the UI uses it to update a button's own text, since the
    polling loop runs on Fusion's single UI thread and nothing repaints
    on its own while Python is sitting inside it.
    '''
    project.StartRendering(job_id)
    try:
        while project.IsRenderingInProgress():
            pct = project.GetRenderJobStatus(job_id).get('CompletionPercentage', 0)
            log('[render] %.0f%%' % pct)
            if on_progress:
                try:
                    on_progress(pct)
                except Exception:
                    pass
            time.sleep(1.5)
        st = project.GetRenderJobStatus(job_id)
        if st.get('JobStatus') != 'Complete':
            raise RuntimeError('Render failed: ' + st.get('JobStatus', 'unknown'))
    finally:
        # The job's only purpose was this one render - leaving it queued is
        # exactly what produces the next render's stale, out-of-range job.
        try:
            project.DeleteRenderJob(job_id)
        except Exception:
            pass


# A colourist works in clips and timelines, not files in a folder - so
# "review before publishing" means putting the render where Resolve's own
# player can scrub it, not opening it in some other application. One
# dedicated timeline is reused for this rather than touching whatever the
# artist actually has open, and is cleared before each review so scrubbing
# an older render is never mistaken for the one about to be published.
REVIEW_TIMELINE_NAME = 'Kitsu Review'


def _find_review_timeline(project):
    for i in range(1, (project.GetTimelineCount() or 0) + 1):
        candidate = project.GetTimelineByIndex(i)
        if candidate and candidate.GetName() == REVIEW_TIMELINE_NAME:
            return candidate
    return None


def clear_review_timeline(project, log=print):
    '''Delete the review timeline and drop its clip, if either exists.

    Called before every render, not only before a fresh review: the review
    timeline's one clip is the *previous* render, at the exact path the new
    render is about to overwrite. Leaving that clip in place is what lets
    Resolve hold the file open, or a stale duration on it, while the new
    render is being written underneath it - the "clip is no longer
    available" failure a shortened re-render was hitting.
    '''
    media_pool = project.GetMediaPool()
    existing = _find_review_timeline(project) if media_pool else None
    if not existing:
        return
    media_pool.DeleteTimelines([existing])
    log('[review] cleared the previous review timeline')


def load_for_review(project, file_path, log=print):
    '''Import a rendered file and load it on a dedicated review timeline.

    Switches the current timeline to it, so the artist can scrub the render
    in Resolve's own player before deciding to publish. Returns that
    timeline. Never touches the timeline that was being worked in - only
    switches away from it, which is undone by picking it again in the
    Edit page once review is done.
    '''
    media_pool = project.GetMediaPool()
    if not media_pool:
        raise RuntimeError('Resolve has no Media Pool for this project.')

    imported = media_pool.ImportMedia([file_path])
    if not imported:
        raise RuntimeError('Resolve could not import the render for review: ' + file_path)
    clip = imported[0]

    clear_review_timeline(project, log=log)

    review_timeline = media_pool.CreateEmptyTimeline(REVIEW_TIMELINE_NAME)
    if not review_timeline:
        raise RuntimeError('Resolve could not create the review timeline.')

    project.SetCurrentTimeline(review_timeline)
    media_pool.AppendToTimeline([clip])

    try:
        get_resolve().OpenPage('edit')
    except Exception:
        pass

    log("[review] loaded on '%s' - reselect your working timeline in the "
       'Edit page when you are done reviewing' % REVIEW_TIMELINE_NAME)
    return review_timeline
