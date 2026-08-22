'''Telling Kitsu about a save.

Saving a version asks for a comment and a status, then posts them on the task
with a viewport grab attached as the preview, which Kitsu also shows as the
task thumbnail. That is the same call the render publish will make later - one
shared ``KitsuClient.publish_preview`` - so a save and a render land in Kitsu
identically, differing only in what image is attached.

The status defaults to leaving the task alone. A work-in-progress save is not
a submission, so it only moves when someone deliberately picks a new one.

The upload runs on a worker thread. A screengrab is small, but the office link
is not always fast and a save must never block on the network: the file is
already on disk by the time this starts, so a failed publish costs a comment,
never work.
'''
from . import capture, prefs, session


def why_not(context, entity_context):
    '''Why this save cannot go to Kitsu, or '' when it can.

    Checked before the dialog opens, so nobody is asked to write a comment
    that has nowhere to go.
    '''
    preferences = prefs.get(context)

    if not preferences or not preferences.publish_on_save:
        return 'publishing on save is switched off'
    if not session.state.connected:
        return 'not connected to Kitsu'
    if entity_context is None or not entity_context.task_id:
        # A context recovered from a filename knows the names but no ids, so
        # there is no task to attach anything to.
        return 'no Kitsu task on this scene'
    return ''


def default_comment(path):
    return '%s saved from Blender' % path.name


def send(context, entity_context, path, comment='', task_status_id=None):
    '''Post a comment and a viewport preview for a version that was saved.

    Returns a short status string for the operator to report. Never raises -
    the save has already happened, and losing the file's identity over a
    network error would be the wrong trade.
    '''
    preferences = prefs.get(context)
    state = session.state

    blocked = why_not(context, entity_context)
    if blocked:
        return blocked

    client = state.client
    task_id = entity_context.task_id
    comment = comment or default_comment(path)

    preview = None
    if preferences.preview_on_save:
        preview = capture.viewport_png(context, preferences.preview_percentage)
        if preview is None:
            comment += ' (no viewport preview available)'

    def work():
        if preview:
            return client.publish_preview(
                task_id, preview, comment=comment,
                task_status_id=task_status_id,
                normalize=bool(getattr(preferences, 'kitsu_normalize', False)),
                log=_log)
        # No image to attach, so just the comment. A status of None means
        # leave it alone, which needs the task's current one posting back.
        status = task_status_id or (client.task(task_id) or {}).get('task_status_id')
        return client._request(
            'POST', 'actions/tasks/%s/comment' % task_id,
            json={'task_status_id': status, 'comment': comment,
                  'checklist': [], 'links': []})

    def done(_result, error):
        if preview:
            capture.discard(preview)
        if error:
            state.say('Kitsu update failed: %s' % error, error=True)
        else:
            state.say('Kitsu updated for %s' % path.name)

    session.run('publishing to Kitsu', work, done, background=True)
    return 'updating Kitsu in the background'


def _log(message):
    print('[BB publish] %s' % message)
