'''Telling Kitsu about a Maya save.

The same shared ``KitsuClient.publish_preview`` the Blender and Nuke sides
call, so a model save, a comp save and a lighting save land on a task
identically.

The picture is a playblast of the viewport - see :mod:`capture` - or a frame
picked out of a render, and it is always a still. A sequence belongs in a
movie and a movie belongs to the host that can colour-manage one; a Maya
publish that quietly turned four renders into an mp4 would be guessing.
'''
import os

from BB_core import settings
from BB_core.kitsu import KitsuError
from BB_core.versioning import tag_comment

from . import session

state = session.state


def why_not(entity_context):
    """Why this save cannot go to Kitsu, or '' when it can."""
    if not settings.get('publish_on_save', True):
        return 'publishing on save is switched off'
    if not state.connected:
        return 'not connected to Kitsu'
    if entity_context is None or not entity_context.task_id:
        return 'no Kitsu task on this scene'
    return ''


def default_comment(path):
    return '%s saved from Maya' % os.path.basename(path)


def send(entity_context, path, comment='', task_status_id=None, preview=None,
         master=False):
    '''Post a comment, a status and optionally a picture.

    Returns a short note. Never raises: the scene is already on disk, and
    losing it to a network error would be the wrong trade.
    '''
    blocked = why_not(entity_context)
    if blocked:
        return blocked

    client = state.client
    text = tag_comment(comment or default_comment(path), entity_context.version)
    if master:
        # The durable record of which version is master, read back for the
        # "Master (v009)" label the browsers show.
        from BB_core.versioning import format_master_tag
        text = '%s %s' % (text, format_master_tag(entity_context.version))

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
