'''Telling Kitsu about a Nuke save.

The same shared ``KitsuClient.publish_preview`` the Blender side calls, so a
comp save and a Blender save land on a task identically.

A snapshot is a real render of one frame through a temporary Write node,
because Nuke has no viewport to grab. That is also why it looks like the comp
rather than like a screenshot of the interface. See :mod:`capture`.
'''
import os

from BB_core import settings
from BB_core.kitsu import KitsuError
from BB_core.versioning import tag_comment

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


def send(entity_context, path, comment='', task_status_id=None, preview=None):
    '''Post a comment, a status and optionally a picture.

    Returns a short note. Never raises: the script is already on disk, and
    losing it to a network error would be the wrong trade.

    ``preview`` goes through the shared publish_preview, the same call the
    Blender side makes - so a comp thumbnail and a 3D one arrive on a task
    identically, and a shot worked on only in Nuke stops being the one with
    no picture.
    '''
    blocked = why_not(entity_context)
    if blocked:
        return blocked

    client = state.client
    text = tag_comment(comment or default_comment(path), entity_context.version)

    try:
        if preview:
            client.publish_preview(
                entity_context.task_id, preview, comment=text,
                task_status_id=task_status_id,
                normalize=bool(settings.get('kitsu_normalize', False)),
                log=lambda message: print('[BB publish] %s' % message))
        else:
            status = task_status_id or (
                client.task(entity_context.task_id) or {}).get('task_status_id')
            client._request(
                'POST', 'actions/tasks/%s/comment' % entity_context.task_id,
                json={'task_status_id': status, 'comment': text,
                      'checklist': [], 'links': []})
    except KitsuError as error:
        state.say('Kitsu update failed: %s' % error, error=True)
        return 'Kitsu update failed: %s' % error

    state.say('Kitsu updated for %s%s'
              % (os.path.basename(path), ' with a snapshot' if preview else ''))
    return 'Kitsu updated'
