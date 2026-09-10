'''Exercise the Maya integration against a real Maya.

Run it with mayapy, which has a licence for ``maya.standalone`` and therefore
a real scene graph, a real fileInfo and a real render globals::

    "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya_check.py

Unlike the Nuke suite, nothing here is stubbed. Nuke needs a render licence to
run headless and so its tests fake the ``nuke`` module; Maya does not, so the
things most worth checking - that a stamp survives a save and reopen, that the
render output lands where the pipeline says - are checked against the
application rather than against a description of it.

There is no viewport under standalone, which is itself worth asserting: the
thumbnail grab has to degrade to a reason rather than an exception.
'''
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)

failures = []


def check(condition, description):
    print('  %s %s' % ('ok  ' if condition else 'FAIL', description))
    if not condition:
        failures.append(description)


def main():
    import maya.standalone
    maya.standalone.initialize(name='python')

    import maya.cmds as cmds

    work_root = tempfile.mkdtemp(prefix='kitsu_maya_')
    os.environ['BB_PIPELINE_SETTINGS'] = os.path.join(work_root, 'settings.json')

    sys.path.insert(0, os.path.join(REPO, 'maya'))
    sys.path.insert(0, REPO)

    import BB_pipeline_maya as package
    from BB_core import settings
    settings.save({'work_root': work_root, 'render_root': work_root,
                   'publish_on_save': True})

    from BB_core.context import EntityContext
    from BB_pipeline_maya import (capture, fetch, publish, review, scenes,
                                  scenesync, session, stamp)

    check(package.core.available,
          'shared core located (%s)' % (package.core.error or 'ok'))
    check(sys.version_info[:2] == (3, 11),
          "running under Maya's Python (%s)" % sys.version.split()[0])
    try:
        import PySide6
        check(True, 'PySide6 is available (%s)' % PySide6.__version__)
    except Exception as error:
        check(False, 'PySide6 is available (%s)' % error)

    # -- the config --------------------------------------------------------
    config = settings.config()
    check(config.dcc('maya').get('ext') == 'ma',
          'Maya writes .ma files (%s)' % config.dcc('maya').get('ext'))
    departments = [str(d).lower() for d in config.dcc('maya').get('departments')]
    check('modeling' in departments and 'rigging' in departments,
          'and authors the 3D departments (%d listed)' % len(departments))

    # -- session: what Maya authors versus what it may look at -------------
    state = session.state
    MODEL = {'id': 'd1', 'name': 'Modeling'}
    COMP = {'id': 'd2', 'name': 'Compositing'}
    state.departments = {d['id']: d for d in (MODEL, COMP)}
    state.task_types = {
        'tt1': {'id': 'tt1', 'name': 'Modeling', 'department_id': 'd1'},
        'tt2': {'id': 'tt2', 'name': 'Compositing', 'department_id': 'd2'},
    }
    state.tasks = [{'id': 't1', 'task_type_id': 'tt1'},
                   {'id': 't2', 'task_type_id': 'tt2'}]
    state.task_departments = session.departments_for_maya()

    modeling, comp = state.task('t1'), state.task('t2')
    check(state.authors(modeling), 'Maya authors a modeling task')
    check(not state.authors(comp), 'and does not author a compositing task')
    browsable = [state.task_type_name(t['task_type_id'])
                 for t in state.browsable_tasks()]
    check(sorted(browsable) == ['Compositing', 'Modeling'],
          'but both are still browsable (%s)' % ', '.join(browsable))
    check(browsable[0] == 'Modeling',
          'with the ones Maya authors offered first')

    # -- the stamp ---------------------------------------------------------
    asset = EntityContext(
        entity_type='asset', project='KitsuTest Project', group='Prop',
        entity='screwdriver', task='Modeling', project_id='p1',
        group_id='g1', entity_id='e1', task_id='t1', task_type_id='tt1',
        version=0, server='http://example:8080')

    check(stamp._unescape(chr(92) + '"a' + chr(92) + chr(92) + 'b') == '"a' + chr(92) + 'b',
          'the unescaper undoes a quote and a backslash')
    check(stamp._unescape('a' + chr(92) + 'nb') == 'a' + chr(10) + 'b',
          'and a newline')
    check(stamp._unescape('a' + chr(92) + 'u00e9') == 'a' + chr(92) + 'u00e9',
          'and leaves a json unicode escape alone for json to decode')

    for extension, kind in (('.ma', 'mayaAscii'), ('.mb', 'mayaBinary')):
        cmds.file(new=True, force=True)
        check(stamp.read() is None,
              'a new scene carries no stamp (%s)' % extension)

        stamped = asset.at_version(7)
        stamp.write(stamped)

        scene = os.path.join(work_root, 'stamped' + extension)
        cmds.file(rename=scene)
        cmds.file(save=True, type=kind, force=True)
        cmds.file(new=True, force=True)
        cmds.file(scene, open=True, force=True)

        back = stamp.read()
        check(back == stamped,
              'the stamp survives a save and reopen (%s)' % extension)
        check(chr(92) + '"' in stamp.raw(),
              'and fileInfo really did escape it (%s)' % extension)

        # The trap this module exists for: fileInfo escapes on set, so a
        # value read and written back would be escaped a second time.
        stamp.write(back)
        check(stamp.read() == stamped,
              're-stamping does not compound the escaping (%s)' % extension)

    with open(os.path.join(work_root, 'stamped.ma'), encoding='utf-8',
              errors='replace') as handle:
        text = handle.read()
    check('fileInfo "BB_pipeline"' in text,
          'and in a .ma the stamp is a greppable line of text')

    # -- naming and versioning --------------------------------------------
    check(scenes.file_type('a.ma') == 'mayaAscii'
          and scenes.file_type('a.mb') == 'mayaBinary',
          'the save format is taken from the extension')

    path, version = scenes.next_version(asset)
    check(version == 1, 'an untouched task starts at v001 (v%03d)' % version)
    check(str(path).endswith('.ma'), 'and the work file is a .ma (%s)' % path.name)
    check('Prop' in path.parts and 'screwdriver' in path.parts,
          'under the asset folders (%s)' % path.parent)

    cmds.file(new=True, force=True)
    made, _note = scenes.create_version(asset)
    check(os.path.isfile(made), 'creating a version writes it (%s)'
          % os.path.basename(made))
    check(stamp.read() is not None and stamp.read().version == 1,
          'and the scene it saved carries the stamp')
    check(not scenes.is_modified(),
          'and is not left looking like it has unsaved changes')

    again, _note = scenes.save_next_version()
    check(os.path.basename(again).endswith('v002.ma'),
          'saving the next version bumps it (%s)' % os.path.basename(again))
    check(stamp.read().version == 2, 'and re-stamps it to match')

    found = fetch.list_versions(asset)
    check([v for v, _p in found] == [1, 2],
          'both versions are listed (%s)' % [v for v, _p in found])

    # -- the render output -------------------------------------------------
    check(scenesync.time_unit(24) == 'film' and scenesync.time_unit(25) == 'pal',
          'the common frame rates use the names Maya writes back')
    check(scenesync.time_unit(23.976) == '23.976fps',
          'and an odd one is spelled the way Maya accepts (%s)'
          % scenesync.time_unit(23.976))

    # Versioning up has already aimed it, which is the point: leaving the
    # output on the previous version is worse than never setting it, because
    # the frames land somewhere plausible and wrong.
    check(scenesync.set_output(stamp.read()) == '',
          'saving a version already aimed the render output')

    computed = cmds.renderSettings(fullPath=True, firstImageName=True)
    computed = (computed or [''])[0]
    expected_dir = str(scenesync.output_path(stamp.read())[0]).replace(chr(92), '/')
    check(computed.startswith(expected_dir),
          'and Maya computes an image path inside it (%s)' % computed)
    check('screwdriver_v002' in computed,
          'named for this version, not the last one (%s)'
          % os.path.basename(computed))

    # Mis-aimed on purpose - the state a scene reopened in a fresh Maya is
    # actually in, because the images file rule lives in the workspace and
    # not in the scene.
    cmds.workspace(fileRule=('images', 'somewhere/else'))
    moved = scenesync.set_output(stamp.read())
    check(bool(moved), 'a workspace pointing elsewhere is re-aimed (%s)' % moved)
    recomputed = (cmds.renderSettings(fullPath=True, firstImageName=True)
                  or [''])[0]
    check(recomputed == computed,
          'back to the same path it had before (%s)' % recomputed)
    check(scenesync.set_output(stamp.read()) == '',
          'and aiming it once more says nothing, because nothing moved')

    # -- frame range, rate and resolution ----------------------------------
    wanted = {'frame_start': 1001, 'frame_end': 1100, 'time_unit': 'pal',
              'resolution_x': 2048, 'resolution_y': 858}
    changed = scenesync.apply(wanted)
    check(len(changed) == 4, 'range, rate and size all applied (%s)' % changed)
    check(int(cmds.playbackOptions(query=True, minTime=True)) == 1001
          and int(cmds.playbackOptions(query=True, maxTime=True)) == 1100,
          'the playback range is set')
    check(int(cmds.getAttr('defaultRenderGlobals.startFrame')) == 1001,
          'and so is the render range a batch actually reads')
    check(cmds.currentUnit(query=True, time=True) == 'pal', 'the rate is set')
    check(int(cmds.getAttr('defaultResolution.width')) == 2048,
          'and the resolution')
    check(abs(cmds.getAttr('defaultResolution.deviceAspectRatio')
              - 2048.0 / 858.0) < 0.001,
          'with the device aspect, so the render is not the wrong shape')
    check(scenesync.differences(wanted) == [],
          'and re-checking finds nothing left to change')

    # -- the thumbnail grab, with no viewport to grab ----------------------
    picture, problem = capture.snapshot()
    check(picture is None and bool(problem),
          'a snapshot without a viewport gives a reason, not an exception (%s)'
          % problem)
    check(capture.describe() == '', 'and describes nothing to snapshot')

    # -- reading a render off disk -----------------------------------------
    render_dir = os.path.join(work_root, 'renders')
    os.makedirs(render_dir, exist_ok=True)
    for frame in range(1001, 1006):
        open(os.path.join(render_dir, 'shot_v001.%04d.png' % frame), 'wb').write(b'x')
    pattern = os.path.join(render_dir, 'shot_v001.####.png')

    frames_found = review.rendered_frames(pattern)
    check(len(frames_found) == 5, 'the rendered frames are found (%d)'
          % len(frames_found))
    check(review.frame_span(pattern, frames_found) == (1001, 1005),
          'and their span is read off the names')

    chosen, problem = review.pick_frame(frames_found)
    check(chosen.endswith('1003.png'),
          'the middle frame is picked, not the first (%s)'
          % os.path.basename(chosen))

    for frame in range(1001, 1004):
        open(os.path.join(render_dir, 'exr_v001.%04d.exr' % frame), 'wb').write(b'x')
    exrs = review.rendered_frames(os.path.join(render_dir, 'exr_v001.####.exr'))
    chosen, problem = review.pick_frame(exrs)
    check(not chosen and 'exr' in problem,
          'an EXR-only render is refused rather than uploaded (%s)' % problem)

    # -- contexts from a selection ----------------------------------------
    state.projects = [{'id': 'p1', 'name': 'Show', 'code': None}]
    state.asset_types = [{'id': 'g1', 'name': 'Prop'}]
    state.assets = [{'id': 'e1', 'name': 'screwdriver', 'entity_type_id': 'g1'}]
    state.sequences = [{'id': 'g2', 'name': 'sc01'}]
    state.shots = [{'id': 'e2', 'name': 'sh01', 'parent_id': 'g2'}]

    built = fetch.current_context('p1', 'g1', 'e1', 't1', True)
    check(built is not None and built.entity_type == 'asset',
          'an asset selection builds an asset context')
    check(built is not None and built.versioned(1) == 'screwdriver_v001',
          'named after the asset (%s)' % (built and built.versioned(1)))

    shot_context = fetch.current_context('p1', 'g2', 'e2', 't1', False)
    check(shot_context is not None and shot_context.entity_type == 'shot',
          'and a shot selection builds a shot context')
    check(fetch.current_context('p1', 'g1', 'nope', 't1', True) is None,
          'an incomplete selection builds nothing rather than guessing')

    # -- publishing --------------------------------------------------------
    check(publish.why_not(None) != '', 'publishing needs a context')
    check('Maya' in publish.default_comment('a_v001.ma'),
          'and says which application it came from')

    # -- the menu ----------------------------------------------------------
    check(package.MENU == 'Kitsu', 'the menu is called Kitsu')
    for name in ('open_browser', 'save_next_version', 'update_kitsu',
                 'publish_render', 'set_output_path', 'apply_frame_range',
                 'open_work_folder', 'open_settings'):
        check(callable(getattr(package, name, None)),
              '%s is reachable from the menu' % name)
    check(package.install_menu() is None,
          'installing the menu in batch does nothing rather than failing')

    # Imported last: it needs Qt, and a failure here should not stop
    # everything above it from having been checked.
    try:
        from BB_pipeline_maya import browser
        check(callable(browser.show_browser), 'the browser module imports')
        check(callable(browser.show_settings), 'and offers its settings dialog')
    except Exception as error:
        check(False, 'the browser module imports (%s)' % error)

    shutil.rmtree(work_root, ignore_errors=True)

    print()
    if failures:
        print('%d FAILED:' % len(failures))
        for description in failures:
            print('  - %s' % description)
        sys.exit(1)
    print('all Maya checks passed')


if __name__ == '__main__':
    main()
