'''Telling Kitsu about a Nuke save.

The same shared ``KitsuClient.publish_preview`` the Blender side calls, so a
comp save and a Blender save land on a task identically.

No viewport grab. Nuke's viewer is not something that can be rendered without
building a Write node and executing it, which is a render rather than a
screengrab - so a save posts a comment and a status, and the picture comes
from the review submit once there is something rendered to send.
'''
import os

from BB_core import settings
from BB_core.kitsu import KitsuError

from . import session

state = session.state


def why_not(entity_context):
    '''Why this save cannot go to Kitsu, or '' when it can.'''
    if not settings.get('publish_on_save', True):
        return 'publishing on save is switched off'
    if not state.connected:
        return 'not connected to Kitsu'
    if entity_context is None or not entity_context.task_id:
        return 'no Kitsu task on this script'
    return ''


def default_comment(path):
    return '%s saved from Nuke' % os.path.basename(path)


def send(entity_context, path, comment='', task_status_id=None):
    '''Post a comment and status for a version that was saved.

    Returns a short note. Never raises: the script is already on disk, and
    losing it to a network error would be the wrong trade.
    '''
    blocked = why_not(entity_context)
    if blocked:
        return blocked

    client = state.client
    text = comment or default_comment(path)

    try:
        status = task_status_id or (
            client.task(entity_context.task_id) or {}).get('task_status_id')
        client._request(
            'POST', 'actions/tasks/%s/comment' % entity_context.task_id,
            json={'task_status_id': status, 'comment': text,
                  'checklist': [], 'links': []})
    except KitsuError as error:
        state.say('Kitsu update failed: %s' % error, error=True)
        return 'Kitsu update failed: %s' % error

    state.say('Kitsu updated for %s' % os.path.basename(path))
    return 'Kitsu updated'
