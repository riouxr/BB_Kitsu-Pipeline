'''Fusion UI for the Kitsu Resolve publisher - UI-A, the Fusion UIManager build.

Kept as a fallback for a Resolve whose embedded Python cannot import PySide -
``__init__.py`` tries ``ui_pyside.py`` (UI-B) first and only reaches this
module if that is unavailable. Several of UIManager's own property names
looked like real Qt properties (``MinimumSize``, ``Weight``, a StyleSheet
string) but did not behave like them, and none of it could be verified
without a live Resolve session to look at - UI-B exists to get away from
that; this file is not being developed further unless UI-B turns out not to
work at all.

The host-side wrapper, the same shape as the Blender and Nuke integrations:
naming, paths, versioning and Kitsu access all live in the shared BB_core
package, and this supplies the Fusion windows and the calls into the Resolve
Project Manager, render queue and Media Pool.

Two windows, Show()/Hide() only - no nested RunLoop, every event handled by
the single main disp.RunLoop, the same pattern the Shot Browser and Assign
windows used before either existed:

  MAIN     login, the assigned shot, and Browse: Project / Sequence / Shot /
           Task, then Open, New Version or New Version From Current -
           exactly Blender's Open / New vNNN / New vNNN from Current Scene,
           applied to a Resolve project instead of a .blend.
  PUBLISH  a small popup: the task Browse assigned (read-only), status,
           render preset, comment, and three explicit steps - Render,
           Review (scrub the render inside Resolve itself before anyone
           outside this machine sees it), then Publish.

Publish was tried as a second tab inside the main window first, hidden and
shown with a plain Visible toggle. Fusion's UIManager never gave that height
back - the same gap a collapsed section leaves - so it is a real second
window instead, sized for its own content, the way every other popup in
this tool already works.

No separate "Assign Shot" flow either: "New Version From Current" replaces
it, and does the same copy-the-open-project trick starting from whatever is
open right now rather than a name picked from a list.
'''
import os
import re

from BB_core import credentials, settings
from BB_core.kitsu import AuthError, KitsuClient, KitsuError, explain

from . import publish, resolve_ops, session, thumbnails

state = session.state

LBL        = 'color:#6e7a9a;font-size:10px;font-weight:bold;'
BTN_BLUE   = 'background:#3a6ff7;color:#fff;font-weight:bold;padding:5px 10px;border-radius:4px;'
BTN_GREEN  = 'background:#2e8b57;color:#fff;font-weight:bold;padding:5px 10px;border-radius:4px;'
BTN_ORANGE = 'background:#c76b1a;color:#fff;font-weight:bold;padding:5px 10px;border-radius:4px;'
BTN_GRAY   = 'padding:5px 10px;border-radius:4px;'
BTN_PURPLE = 'background:#7a4fd6;color:#fff;font-weight:bold;padding:5px 10px;border-radius:4px;'
BTN_PUBLISH = ('background:#3a6ff7;color:#fff;font-weight:bold;'
              'font-size:13px;padding:8px;border-radius:5px;')
# A hardcoded background: in a widget's own StyleSheet overrides Qt's normal
# automatic dimming for a disabled button, so ".Enabled = False" alone left
# every button looking exactly as clickable as when it was enabled. Swapped
# in by hand instead of trusting a ":disabled" selector this wrapper may or
# may not honour.
BTN_DISABLED = 'background:#242530;color:#565f70;padding:5px 10px;border-radius:4px;'

# Offered first in the Task list on the Browse tab, for a studio where this
# tool is mainly used for grading. Falls back to plain server order when the
# project has no task by this name.
DEFAULT_TASK_NAME = 'Color Grading'


def main():
    resolve = resolve_ops.get_resolve()
    if not resolve:
        print('ERROR: run from inside Resolve  Workspace > Scripts')
        return

    fusion = resolve.Fusion()
    ui     = fusion.UIManager
    disp   = bmd.UIDispatcher(ui)  # noqa: F821

    s = settings.load()
    state.reset()

    # UI-only state. Kitsu context itself lives on the shared `state` object
    # so publish.py can reach it without the UI.
    ui_state = {
        'br_sequences':      [],
        'br_shots':          [],
        'br_tasks':          [],
        'br_render_versions': [],
        'br_version_mode':    None,
        'br_selected_version': None,
        'br_pending_shot':   None,
        'current_task_id':   None,
        'version':           1,
        'project_base':      None,
        'project_name':      None,
        'last_render_path':  None,
        'working_timeline_name': None,
        'reviewed':          False,
        'published':         False,
    }

    # ── Main window ───────────────────────────────────────────────────────────
    win = disp.AddWindow(
        {'ID': 'KitsuWin', 'WindowTitle': 'Kitsu Publisher',
         'Geometry': [100, 80, 480, 800]},
        [ui.VGroup({'Spacing': 5}, [
            ui.Label({'Text': 'ACCOUNT', 'StyleSheet': LBL}),
            ui.VGroup({'ID': 'LoginGroup', 'Spacing': 4}, [
                ui.HGroup({'Spacing': 6, 'Weight': 0}, [
                    ui.Label({'Text': 'SERVER', 'StyleSheet': LBL, 'FixedWidth': 62}),
                    ui.LineEdit({'ID': 'Server', 'Text': s.get('server', ''),
                                'PlaceholderText': 'http://192.168.x.x'}),
                ]),
                ui.HGroup({'Spacing': 6, 'Weight': 0}, [
                    ui.Label({'Text': 'EMAIL', 'StyleSheet': LBL, 'FixedWidth': 62}),
                    ui.LineEdit({'ID': 'Email', 'Text': s.get('email', ''),
                                'PlaceholderText': 'artist@studio.com'}),
                ]),
                ui.HGroup({'Spacing': 6, 'Weight': 0}, [
                    ui.Label({'Text': 'PASSWORD', 'StyleSheet': LBL, 'FixedWidth': 62}),
                    ui.LineEdit({'ID': 'Pwd', 'EchoMode': 'Password',
                                'PlaceholderText': 'password'}),
                ]),
                ui.Button({'ID': 'LoginBtn',  'Text': 'Sign in',
                          'FixedHeight': 28,  'StyleSheet': BTN_BLUE}),
            ]),
            ui.Label({'ID': 'StatusLbl',  'Text': '', 'WordWrap': True,
                     'StyleSheet': 'color:#e05a6a;font-size:11px;'}),

            ui.HGroup({'Spacing': 6, 'Weight': 0}, [
                ui.Label({'Text': 'PROJECT', 'StyleSheet': LBL, 'FixedWidth': 62}),
                ui.ComboBox({'ID': 'BrProj'}),
            ]),
            ui.HGroup({'Spacing': 6, 'Weight': 0}, [
                ui.Label({'Text': 'SEQUENCE', 'StyleSheet': LBL, 'FixedWidth': 62}),
                ui.ComboBox({'ID': 'BrSeq'}),
            ]),
            ui.HGroup({'Spacing': 6, 'Weight': 0}, [
                ui.Tree({
                    'ID': 'BrTree', 'SortingEnabled': False,
                    'AlternatingRowColors': True, 'RootIsDecorated': False,
                    'SelectionMode': 'SingleSelection', 'UniformRowHeights': True,
                    'FixedHeight': 130,
                }),
                # Enabled, not wired to any click handler - Qt auto-desaturates
                # a disabled button's icon, which would leave a "greyed out"
                # looking thumbnail even though nothing else about it is off.
                ui.Button({'ID': 'BrThumb', 'Text': '',
                          'Flat': True, 'FixedSize': [150, 84], 'IconSize': [150, 84],
                          'StyleSheet': 'background:#111318;border-radius:4px;',
                          'Weight': 0}),
            ]),
            ui.HGroup({'Spacing': 6, 'Weight': 0}, [
                ui.Label({'Text': 'TASK', 'StyleSheet': LBL, 'FixedWidth': 62}),
                ui.ComboBox({'ID': 'BrTask', 'Enabled': False}),
            ]),
            ui.Label({'ID': 'BrStatusLbl', 'Text': '', 'WordWrap': True,
                     'MinimumSize': [0, 32],
                     'StyleSheet': 'color:#8a9a8a;font-size:11px;'}),
            # Resolve project versions for a task Resolve authors, or
            # rendered sequence versions for a "(renders)" one - never both,
            # exactly which the Task combo's own label already said. Click a
            # row before Open or Import will do anything.
            ui.Tree({
                'ID': 'BrVersionTree', 'SortingEnabled': False,
                'AlternatingRowColors': True, 'RootIsDecorated': False,
                'SelectionMode': 'SingleSelection', 'UniformRowHeights': True,
                # FixedHeight, not MinimumSize - with only a row or two of
                # versions the tree was rendering shorter than its minimum,
                # and the button row below it did not account for the
                # shortfall, overlapping into it. Tall enough for ~5 rows
                # rather than the 2 the original height showed.
                'FixedHeight': 180,
            }),
            ui.HGroup({'Spacing': 6, 'Weight': 0}, [
                ui.Button({'ID': 'BrOpenBtn', 'Text': 'Open',
                          'Enabled': False, 'FixedHeight': 30, 'StyleSheet': BTN_DISABLED}),
                ui.Button({'ID': 'BrNewBtn', 'Text': 'New Version',
                          'Enabled': False, 'FixedHeight': 30, 'StyleSheet': BTN_DISABLED}),
                ui.Button({'ID': 'BrNewFromCurrentBtn', 'Text': 'New From Current',
                          'Enabled': False, 'FixedHeight': 30, 'StyleSheet': BTN_DISABLED}),
            ]),
            ui.Button({'ID': 'BrImportBtn', 'Text': 'Import in Media Storage',
                      'Enabled': False, 'FixedHeight': 28, 'MaximumHeight': 28,
                      'StyleSheet': BTN_DISABLED}),

            # A thin rule, not a button - Open/New/New From Current/Import
            # are what Browse does; Publish is the next, separate step once
            # one of those has assigned a shot, so it sits below a visible
            # break rather than at the top where "the last step" would read
            # as "the first thing to click".
            ui.Label({'Text': '', 'FixedHeight': 28, 'MaximumHeight': 28}),
            ui.Button({'ID': 'OpenPublishBtn', 'Text': 'Publish...',
                      'FixedHeight': 28, 'MaximumHeight': 28, 'StyleSheet': BTN_BLUE}),
        ])]
    )
    itm = win.GetItems()

    hdr = itm['BrTree'].NewItem()
    hdr.Text[0] = 'Shot'
    itm['BrTree'].SetHeaderItem(hdr)
    itm['BrTree'].ColumnWidth[0] = 230

    vhdr = itm['BrVersionTree'].NewItem()
    vhdr.Text[0] = 'Version'
    vhdr.Text[1] = 'Detail'
    itm['BrVersionTree'].SetHeaderItem(vhdr)
    itm['BrVersionTree'].ColumnWidth[0] = 60

    # ── Publish window (popup - Show/Hide only) ─────────────────────────────────
    pwin = disp.AddWindow(
        {'ID': 'PublishWin', 'WindowTitle': 'Publish to Kitsu',
         'Geometry': [600, 80, 380, 340]},
        [ui.VGroup({'Spacing': 5}, [
            ui.Label({'ID': 'PubTaskLbl', 'Text': 'TASK: (none)',
                     'StyleSheet': 'color:#c8a84b;font-size:11px;font-weight:bold;'}),

            ui.Label({'Text': 'STATUS', 'StyleSheet': LBL}),
            ui.ComboBox({'ID': 'TaskStatus', 'Enabled': False}),

            ui.Label({'Text': 'RENDER PRESET', 'StyleSheet': LBL}),
            ui.ComboBox({'ID': 'Preset'}),

            ui.Label({'Text': 'COMMENT', 'StyleSheet': LBL}),
            ui.TextEdit({'ID': 'Comment', 'MinimumSize': [0, 50]}),

            ui.HGroup({'Spacing': 6, 'Weight': 0}, [
                ui.Button({'ID': 'RenderBtn', 'Text': '1. Render',
                          'Enabled': False, 'FixedHeight': 34, 'StyleSheet': BTN_DISABLED}),
                ui.Button({'ID': 'ReviewBtn', 'Text': '2. Review in Resolve',
                          'Enabled': False, 'FixedHeight': 34, 'StyleSheet': BTN_DISABLED}),
            ]),
            ui.Button({'ID': 'PublishBtn', 'Text': '3. Publish to Kitsu',
                      'Enabled': False, 'FixedHeight': 38,
                      'StyleSheet': BTN_DISABLED}),
            ui.Label({'ID': 'PubStepsLbl', 'Text': '', 'WordWrap': True,
                     'StyleSheet': 'color:#8a9a8a;font-size:11px;'}),
        ])]
    )
    pitm = pwin.GetItems()

    # ── Generic helpers ──────────────────────────────────────────────────────
    def pump():
        '''Force Fusion's Qt event loop to process one round of paint events.

        Render polls with a Python-side ``time.sleep`` loop on this same UI
        thread, and nothing repaints on its own while Python is sitting
        inside it - a log line set mid-render otherwise only appears once
        the whole operation finishes. Resolve's own scripting UI is built on
        Qt, so its ``QApplication`` is already running; this just asks it to
        catch up before Python goes back to sleep. Safe to call anywhere -
        silently does nothing if the widget toolkit is not what was assumed.
        '''
        for pyside in ('PySide6', 'PySide2', 'PySide'):
            try:
                module = __import__(pyside, fromlist=['QtWidgets'])
                app = module.QtWidgets.QApplication.instance()
                if app:
                    app.processEvents()
                return
            except Exception:
                continue

    def log(msg):
        # No in-window log widget - this still reaches Resolve's own Python
        # console, which is where render/publish progress and errors show up.
        print(msg)
        pump()

    def setstatus(msg, ok=False):
        itm['StatusLbl'].Text = msg
        itm['StatusLbl'].StyleSheet = (
            'color:#4fbb6a;font-size:11px;' if ok else 'color:#e05a6a;font-size:11px;')

    def fill(target, cb_id, lst, key='name', placeholder='select'):
        cb = target[cb_id]; cb.Clear(); cb.AddItem(placeholder)
        for x in lst: cb.AddItem(x[key])
        cb.Enabled = bool(lst)

    def set_btn(target, widget_id, enabled, active_style):
        '''Enable/disable a button and make it actually look it.

        ``.Enabled`` alone does not grey a button out here - see
        BTN_DISABLED above - so every button toggle goes through this
        instead of setting ``.Enabled`` directly.
        '''
        target[widget_id].Enabled = enabled
        target[widget_id].StyleSheet = active_style if enabled else BTN_DISABLED

    def presets():
        try:    p = resolve_ops.get_current_project().GetRenderPresetList() or []
        except Exception: p = []
        pitm['Preset'].Clear(); pitm['Preset'].AddItem('H.264 default')
        for x in p: pitm['Preset'].AddItem(x)
    presets()

    # ── Assigned task / project ──────────────────────────────────────────────
    def current_task():
        tid = ui_state['current_task_id']
        if not tid: return None
        return next((t for t in state.shot_tasks if t.get('id') == tid), None)

    def refresh_statuses():
        '''Only the statuses the current project actually uses.'''
        fill(pitm, 'TaskStatus', state.project_statuses(), placeholder='no change')
        pitm['TaskStatus'].Enabled = True
        for i, x in enumerate(state.project_statuses()):
            if x.get('short_name', '').upper() in ('WFA', 'WAITING FOR APPROVAL'):
                pitm['TaskStatus'].CurrentIndex = i + 1; break

    def update_publish_task_label():
        task = current_task()
        pitm['PubTaskLbl'].Text = (
            'TASK: ' + state.task_type_name(task.get('task_type_id'))
            if task else 'TASK: (none)')

    def update_render_review_publish_btns():
        ready = bool(state.client and ui_state['project_name'] and state.shot and current_task())
        set_btn(pitm, 'RenderBtn', ready, BTN_GREEN)
        has_render = ready and bool(ui_state['last_render_path'])
        set_btn(pitm, 'ReviewBtn', has_render, BTN_ORANGE)
        set_btn(pitm, 'PublishBtn', has_render, BTN_PUBLISH)
        update_steps_label()

    def update_steps_label():
        '''What has actually happened for the render currently on disk.'''
        rendered  = bool(ui_state['last_render_path'])
        reviewed  = ui_state['reviewed']
        published = ui_state['published']

        def mark(done, label):
            return ('✓ ' if done else '○ ') + label

        pitm['PubStepsLbl'].Text = '   '.join([
            mark(rendered, 'Rendered'),
            mark(reviewed, 'Reviewed'),
            mark(published, 'Published'),
        ])
        pitm['PubStepsLbl'].StyleSheet = (
            'color:#4fbb6a;font-size:11px;' if published else
            'color:#8a9a8a;font-size:11px;')

    def set_assigned(name):
        ui_state['project_name']     = name
        ui_state['version']          = resolve_ops.version_from_name(name)
        ui_state['last_render_path'] = None
        ui_state['working_timeline_name'] = None
        ui_state['reviewed']         = False
        ui_state['published']        = False
        pitm['RenderBtn'].Text = '1. Render'
        remember_working_timeline()
        update_publish_task_label()
        update_render_review_publish_btns()

    def remember_working_timeline():
        '''Note whatever timeline is current right now as "the" working one.

        Kept as a **name**, not the Timeline object Resolve handed back -
        that object can go stale across the render/import/delete calls that
        happen between remembering it and needing it again. A name survives
        all of that; it is re-looked-up fresh every time it is needed.

        Called as soon as a project is opened or created, and again before
        every render - so Review switching away to ``Kitsu Review`` never
        overwrites what "working timeline" means, but a timeline the artist
        genuinely created or switched to always does.
        '''
        try:
            project = resolve_ops.get_current_project()
            current = project.GetCurrentTimeline() if project else None
            name = current.GetName() if current else None
        except Exception:
            name = None
        if name and name != resolve_ops.REVIEW_TIMELINE_NAME:
            ui_state['working_timeline_name'] = name

    def restore_working_timeline(project):
        '''Switch back to the working timeline, e.g. after Review or Publish.

        Looks the timeline up by name at the moment it is needed, rather
        than trusting a handle captured earlier, and actually checks
        whether the switch succeeded instead of assuming it did.
        '''
        name = ui_state.get('working_timeline_name')
        if not name:
            return
        try:
            current = project.GetCurrentTimeline()
            if current and current.GetName() == name:
                return
            for i in range(1, (project.GetTimelineCount() or 0) + 1):
                candidate = project.GetTimelineByIndex(i)
                if candidate and candidate.GetName() == name:
                    if project.SetCurrentTimeline(candidate):
                        log('[resolve] back on your working timeline (%s)' % name)
                    else:
                        log('[resolve] ERROR: Resolve refused to switch back to "%s"' % name)
                    return
            log('[resolve] ERROR: working timeline "%s" no longer exists' % name)
        except Exception as e:
            log('[resolve] could not restore working timeline: %s' % e)

    def load_tasks_for_shot(shot):
        log('[kitsu] loading tasks...')
        try:
            tasks = state.client.tasks_for_shot(shot['id'])
        except KitsuError as e:
            log('[kitsu] error: ' + str(e)); return
        state.shot_tasks = state.sort_tasks(tasks)
        update_publish_task_label()
        update_render_review_publish_btns()

    # ── Login ─────────────────────────────────────────────────────────────────
    def do_login():
        server = itm['Server'].Text.strip()
        email  = itm['Email'].Text.strip()
        typed  = itm['Pwd'].Text.strip()
        pwd    = typed or (credentials.get_password(email) if email else None)
        if not (server and email and pwd):
            setstatus('Fill in the server, email and password.'); return
        setstatus('Signing in...')
        try:
            client = KitsuClient(server, verify=not settings.get('allow_insecure_tls', False))
            client.log_in(email, pwd)
        except (AuthError, KitsuError) as e:
            setstatus(explain(e, server, email)); return
        except Exception as e:
            import traceback
            setstatus('Login failed: ' + str(e))
            log('[login error]\n' + traceback.format_exc()); return

        state.client = client
        setstatus('Connected ✓', ok=True)
        settings.save({'server': server, 'email': email})
        if typed and credentials.available():
            credentials.set_password(email, typed)
        itm['Pwd'].Text = ''
        _load_initial()

    def _load_initial():
        c = state.client
        log('[kitsu] loading data...')
        try:
            state.projects    = c.open_projects()
            state.task_types  = {t['id']: t for t in c.task_types()}
            state.departments = {d['id']: d for d in c.departments()}
            state.statuses    = c.task_statuses()
        except KitsuError as e:
            log('[kitsu] error: ' + str(e)); return
        state.load_departments_filter()
        fill(pitm, 'TaskStatus', state.statuses, placeholder='no change')
        pitm['TaskStatus'].Enabled = True
        log('[kitsu] %d project(s) loaded' % len(state.projects))

        fill(itm, 'BrProj', state.projects, placeholder='select project')
        saved_p = settings.get('last_project')
        for i, p in enumerate(state.projects):
            if p['id'] == saved_p:
                itm['BrProj'].CurrentIndex = i + 1
                _br_load_seqs(); break

        detect_current_project()

    # ── Auto-detect if current Resolve project is already a Kitsu project ───────
    def detect_current_project():
        '''
        If the currently open Resolve project is named
        proj_seq_shot_task_vNNN, try to match it against loaded Kitsu data
        and restore the context - project, sequence, shot AND task.
        '''
        try:
            curr_name = resolve_ops.get_current_project().GetName()
        except Exception:
            return
        m = re.match(r'^(.+)_v(\d+)$', curr_name, re.IGNORECASE)
        if not m:
            log("[detect] '%s' doesn't look like a Kitsu project name" % curr_name)
            return
        base = m.group(1)
        ver  = int(m.group(2))
        parts = base.rsplit('_', 3)
        if len(parts) != 4:
            log("[detect] can't parse base '%s' into proj/seq/shot/task" % base); return
        proj_san, seq_san, shot_san, task_san = parts
        log('[detect] looking for proj=%s seq=%s shot=%s task=%s v=%s'
           % (proj_san, seq_san, shot_san, task_san, ver))

        c = state.client
        for proj in state.projects:
            if resolve_ops.sanitize(proj['name']) != proj_san: continue
            try:
                seqs = c.sequences(proj['id'])
            except KitsuError: continue
            for seq in seqs:
                if resolve_ops.sanitize(seq['name']) != seq_san: continue
                try:
                    shots = c.shots(seq['id'])
                except KitsuError: continue
                for shot in shots:
                    if resolve_ops.sanitize(shot['name']) != shot_san: continue
                    try:
                        tasks = c.tasks_for_shot(shot['id'])
                    except KitsuError: continue
                    for task in tasks:
                        task_name = state.task_type_name(task.get('task_type_id'))
                        if resolve_ops.sanitize(task_name) != task_san: continue
                        state.project, state.sequence, state.shot = proj, seq, shot
                        ui_state['project_base']    = base
                        ui_state['version']         = ver
                        ui_state['current_task_id'] = task['id']
                        settings.save({'last_task': task['id']})
                        refresh_statuses()
                        set_assigned(curr_name)
                        load_tasks_for_shot(shot)
                        log("[detect] ✓ matched '%s' → %s / %s / %s / %s"
                           % (curr_name, proj['name'], seq['name'], shot['name'], task_name))
                        return
        log("[detect] no Kitsu match found for '%s'" % curr_name)

    # ── Browse ────────────────────────────────────────────────────────────────
    def _br_load_seqs():
        idx = itm['BrProj'].CurrentIndex - 1
        if idx < 0: return
        proj = state.projects[idx]
        try:
            ui_state['br_sequences'] = state.client.sequences(proj['id'])
        except KitsuError as e:
            log('[browse] ' + str(e)); return
        fill(itm, 'BrSeq', ui_state['br_sequences'], placeholder='select sequence')
        saved = settings.get('last_sequence')
        for i, sq in enumerate(ui_state['br_sequences']):
            if sq['id'] == saved:
                itm['BrSeq'].CurrentIndex = i + 1
                _br_load_shots(); break

    def _br_load_shots():
        idx = itm['BrSeq'].CurrentIndex - 1
        if idx < 0: return
        seq = ui_state['br_sequences'][idx]
        try:
            ui_state['br_shots'] = state.client.shots(seq['id'])
        except KitsuError as e:
            log('[browse] ' + str(e)); return
        _br_rebuild_tree()

    def _br_rebuild_tree():
        tree = itm['BrTree']
        tree.Clear()
        _br_clear_selection()
        thumbnails.apply(ui, itm['BrThumb'], state.client, None)
        for shot in ui_state['br_shots']:
            row = tree.NewItem()
            row.Text[0] = shot.get('name', shot['id'])
            tree.AddTopLevelItem(row)

    def _br_clear_selection():
        ui_state['br_pending_shot'] = None
        ui_state['br_tasks']        = []
        itm['BrTask'].Clear()
        itm['BrTask'].Enabled = False
        _br_refresh_versions()

    def _br_load_tasks(shot):
        try:
            tasks = state.client.tasks_for_shot(shot['id'])
        except KitsuError as e:
            log('[browse] ' + str(e)); return
        ui_state['br_tasks'] = state.sort_tasks(tasks)
        itm['BrTask'].Clear()
        itm['BrTask'].AddItem('select task')
        default_idx = -1
        for i, t in enumerate(ui_state['br_tasks']):
            # Same marker Nuke's browser uses: a task this DCC is not
            # configured to author still shows up, so its renders can be
            # found, but it is labelled as someone else's work.
            task_name = state.task_type_name(t.get('task_type_id'))
            label = task_name
            if not state.authors(t):
                label += '  (renders)'
            itm['BrTask'].AddItem(label)
            if task_name.strip().lower() == DEFAULT_TASK_NAME.lower():
                default_idx = i
        itm['BrTask'].Enabled = bool(ui_state['br_tasks'])
        if default_idx >= 0:
            itm['BrTask'].CurrentIndex = default_idx + 1
        _br_refresh_versions()

    def _br_current_task():
        idx = itm['BrTask'].CurrentIndex - 1
        tasks = ui_state['br_tasks']
        return tasks[idx] if 0 <= idx < len(tasks) else None

    def _br_current_base():
        shot = ui_state['br_pending_shot']
        task = _br_current_task()
        idx_p = itm['BrProj'].CurrentIndex - 1
        idx_s = itm['BrSeq'].CurrentIndex - 1
        if not shot or not task or idx_p < 0 or idx_s < 0:
            return None
        proj = state.projects[idx_p]
        seq  = ui_state['br_sequences'][idx_s]
        task_name = state.task_type_name(task.get('task_type_id'))
        return resolve_ops.build_project_base(proj['name'], seq['name'], shot['name'], task_name)

    def _br_reset_version_ui():
        itm['BrVersionTree'].Clear()
        itm['BrStatusLbl'].Text = ''
        set_btn(itm, 'BrOpenBtn', False, BTN_BLUE)
        set_btn(itm, 'BrNewBtn', False, BTN_GREEN)
        set_btn(itm, 'BrNewFromCurrentBtn', False, BTN_ORANGE)
        set_btn(itm, 'BrImportBtn', False, BTN_PURPLE)
        ui_state['br_version_mode']     = None
        ui_state['br_selected_version'] = None
        ui_state['br_render_versions']  = []

    def _br_refresh_versions():
        '''Fill the version list for the browsed shot + task.

        A task Resolve authors lists its Resolve project versions - Open,
        New Version and New From Current act on them. A "(renders)" task
        lists rendered sequence versions instead - only Import applies -
        exactly the split Nuke's browser makes between a task with a script
        to open and one that only has frames on the render root.
        '''
        _br_reset_version_ui()

        base = _br_current_base()
        shot = ui_state['br_pending_shot']
        task = _br_current_task()
        idx_p = itm['BrProj'].CurrentIndex - 1
        idx_s = itm['BrSeq'].CurrentIndex - 1
        if not base or not shot or not task or idx_p < 0 or idx_s < 0:
            return

        proj = state.projects[idx_p]
        seq  = ui_state['br_sequences'][idx_s]
        tree = itm['BrVersionTree']

        if state.authors(task):
            ui_state['br_version_mode'] = 'project'
            existing = resolve_ops.get_all_resolve_project_names()
            matches = resolve_ops.matching_versions(base, existing)  # oldest first
            for version, name in reversed(matches):
                row = tree.NewItem()
                row.Text[0] = 'v%03d' % version
                row.Text[1] = name
                tree.AddTopLevelItem(row)

            itm['BrStatusLbl'].Text = (
                '%d Resolve project version(s) — click one to Open' % len(matches)
                if matches else 'No Resolve project yet for this shot + task')
            set_btn(itm, 'BrNewBtn', True, BTN_GREEN)
            try:
                has_current = bool(resolve_ops.get_current_project())
            except Exception:
                has_current = False
            set_btn(itm, 'BrNewFromCurrentBtn', has_current, BTN_ORANGE)

        else:
            ui_state['br_version_mode'] = 'render'
            try:
                renders = publish.render_versions_for(proj, seq, shot, task)  # oldest first
            except Exception as e:
                # Never swallowed silently: a missing or mismatched render
                # root has to be visible, or "nothing found" reads
                # identically to "not rendered yet" when the real problem
                # is a config that points somewhere else entirely.
                itm['BrStatusLbl'].Text = 'Could not check for renders: %s' % e
                return

            ui_state['br_render_versions'] = renders
            for version, _pattern, first, last in reversed(renders):
                row = tree.NewItem()
                row.Text[0] = 'v%03d' % version
                row.Text[1] = '%d frame(s)' % (last - first + 1)
                tree.AddTopLevelItem(row)

            if renders:
                itm['BrStatusLbl'].Text = (
                    '%d rendered version(s) — click one to Import' % len(renders))
            else:
                try:
                    folder = publish.render_folder_for(proj, seq, shot, task, 1)
                    looked_in = os.path.dirname(folder)
                except Exception:
                    looked_in = '(could not resolve a path)'
                itm['BrStatusLbl'].Text = 'No rendered sequence found. Looked in:\n' + looked_in

    def on_br_version_click(ev):
        item = ev.get('item') or ev.get('Item')
        if item is None: return
        try:
            version = int(item.Text[0].lstrip('vV'))
        except (ValueError, AttributeError):
            return
        ui_state['br_selected_version'] = version
        if ui_state['br_version_mode'] == 'project':
            set_btn(itm, 'BrOpenBtn', True, BTN_BLUE)
        elif ui_state['br_version_mode'] == 'render':
            set_btn(itm, 'BrImportBtn', True, BTN_PURPLE)

    def _br_finish(proj, seq, shot, task, name):
        state.project, state.sequence, state.shot = proj, seq, shot
        ui_state['current_task_id'] = task['id']
        settings.save({'last_project': proj['id'], 'last_sequence': seq['id'],
                       'last_shot': shot['id'], 'last_task': task['id']})
        refresh_statuses()
        set_assigned(name)
        load_tasks_for_shot(shot)
        pwin.Show()

    def on_br_action(ev):
        w = ev['who']
        shot = ui_state['br_pending_shot']
        task = _br_current_task()
        idx_p = itm['BrProj'].CurrentIndex - 1
        idx_s = itm['BrSeq'].CurrentIndex - 1
        if not shot or not task or idx_p < 0 or idx_s < 0: return
        proj = state.projects[idx_p]
        seq  = ui_state['br_sequences'][idx_s]
        task_name = state.task_type_name(task.get('task_type_id'))
        base = resolve_ops.build_project_base(proj['name'], seq['name'], shot['name'], task_name)
        existing = resolve_ops.get_all_resolve_project_names()
        pm = resolve_ops.get_project_manager()

        if w == 'BrOpenBtn':
            ver = ui_state['br_selected_version']
            if not ver: return
            name = '%s_v%03d' % (base, ver)
            log("[resolve] opening '%s'..." % name)
            if not pm.LoadProject(name):
                log("[resolve] ERROR: could not open '%s'" % name); return
            log("[resolve] '%s' opened ✓" % name)
            ui_state['version'] = ver

        elif w == 'BrNewBtn':
            name, ver = resolve_ops.next_version_name(base, existing)
            log("[resolve] creating '%s'..." % name)
            if not pm.CreateProject(name):
                log("[resolve] ERROR: could not create '%s'" % name); return
            log("[resolve] '%s' created & opened ✓" % name)
            ui_state['version'] = ver

        elif w == 'BrNewFromCurrentBtn':
            try:
                src_proj = pm.GetCurrentProject()
                src = src_proj.GetName() if src_proj else None
            except Exception:
                src = None
            if not src:
                log('[resolve] ERROR: no project currently open to copy from'); return
            name, ver = resolve_ops.next_version_name(base, existing)
            log("[resolve] copying '%s'  →  '%s'..." % (src, name))
            if not resolve_ops.copy_resolve_project(src, name, log=log):
                log('[resolve] ERROR: project copy failed'); return
            log("[resolve] '%s' ready ✓  ('%s' untouched)" % (name, src))
            ui_state['version'] = ver

        else:
            return

        _br_finish(proj, seq, shot, task, name)

    def on_br_import(ev):
        '''Bring the version picked in the version list into the Media Pool
        - independent of Open/New/New From Current, since a renders-only
        task (Lighting, a Compositing pass) has no Resolve project to open
        at all, only frames on the render root.
        '''
        shot = ui_state['br_pending_shot']
        task = _br_current_task()
        idx_p = itm['BrProj'].CurrentIndex - 1
        idx_s = itm['BrSeq'].CurrentIndex - 1
        version = ui_state['br_selected_version']
        if not shot or not task or idx_p < 0 or idx_s < 0 or not version:
            return
        proj = state.projects[idx_p]
        seq  = ui_state['br_sequences'][idx_s]

        set_btn(itm, 'BrImportBtn', False, BTN_PURPLE)
        itm['BrImportBtn'].Text = 'Importing...'
        try:
            folder = publish.render_folder_for(proj, seq, shot, task, version)
            log('[import] %s' % folder)
            resolve_ops.import_sequence_folder(folder, log=log)
            log('[import] done ✓')
        except Exception as e:
            log('[error] ' + str(e))
        itm['BrImportBtn'].Text = 'Import in Media Storage'
        set_btn(itm, 'BrImportBtn', True, BTN_PURPLE)

    def on_br_combo(ev):
        w = ev['who']
        if   w == 'BrProj': _br_load_seqs()
        elif w == 'BrSeq':  _br_load_shots()
        elif w == 'BrTask': _br_refresh_versions()

    def on_br_tree_click(ev):
        item = ev.get('item') or ev.get('Item')
        if item is None: return
        name = item.Text[0]
        for shot in ui_state['br_shots']:
            if shot.get('name') == name:
                ui_state['br_pending_shot'] = shot
                thumbnails.apply(ui, itm['BrThumb'], state.client, shot)
                _br_load_tasks(shot)
                break

    # ── Render / Review / Publish ────────────────────────────────────────────
    def do_render():
        task = current_task()
        if not task: log('[error] no task assigned - use Browse first'); return
        pi = pitm['Preset'].CurrentIndex
        preset = pitm['Preset'].GetItem(pi) if pi > 0 else None

        def on_progress(pct):
            pitm['RenderBtn'].Text = 'Rendering... %d%%' % int(pct)
            pump()

        set_btn(pitm, 'RenderBtn', False, BTN_GREEN)
        set_btn(pitm, 'ReviewBtn', False, BTN_ORANGE)
        set_btn(pitm, 'PublishBtn', False, BTN_PUBLISH)
        ui_state['last_render_path'] = None
        ui_state['reviewed']  = False
        ui_state['published'] = False
        project = resolve_ops.get_current_project()

        # If Review left the current timeline on "Kitsu Review" (or anything
        # else), always render the timeline that was actually being worked
        # in - never the auxiliary review one, whose own clip is about to be
        # the very file this render overwrites.
        remember_working_timeline()
        restore_working_timeline(project)

        # The review timeline's one clip is the *previous* render, at the
        # exact path this render is about to overwrite - drop it before
        # rendering, not only before the next Review, or Resolve can still
        # be holding that file open or showing a now-wrong duration for it.
        try:
            resolve_ops.clear_review_timeline(project, log=log)
        except Exception as e:
            log('[review] could not clear the old review timeline: %s' % e)

        try:
            path = publish.render(project, task, ui_state['version'], preset,
                                  log=log, on_progress=on_progress)
            ui_state['last_render_path'] = path
            log('[render] done ✓ — review it, then publish')
        except Exception as e:
            log('[error] ' + str(e))
        pitm['RenderBtn'].Text = '1. Render'
        update_render_review_publish_btns()

    def do_review():
        path = ui_state['last_render_path']
        if not path: log('[error] render first'); return
        set_btn(pitm, 'ReviewBtn', False, BTN_ORANGE)
        pitm['ReviewBtn'].Text = 'Loading...'
        try:
            resolve_ops.load_for_review(resolve_ops.get_current_project(), path, log=log)
            ui_state['reviewed'] = True
            log('[review] ✓ ready to scrub in Resolve')
        except Exception as e:
            log('[error] ' + str(e))
        pitm['ReviewBtn'].Text = '2. Review in Resolve'
        update_render_review_publish_btns()

    def do_publish():
        task = current_task()
        path = ui_state['last_render_path']
        if not task: log('[error] no task assigned'); return
        if not path: log('[error] render (and review) before publishing'); return

        statuses = state.project_statuses()
        idx_st = pitm['TaskStatus'].CurrentIndex - 1
        status = statuses[idx_st] if 0 <= idx_st < len(statuses) else None
        comment = pitm['Comment'].PlainText.strip()

        settings.save({'last_task': task['id']})
        set_btn(pitm, 'PublishBtn', False, BTN_PUBLISH)
        pitm['PublishBtn'].Text = 'Publishing...'
        try:
            publish.upload(task, status, comment, path, log=log)
            ui_state['published'] = True
            log('[kitsu] published ✓')
            restore_working_timeline(resolve_ops.get_current_project())
        except Exception as e:
            log('[error] ' + str(e))
        pitm['PublishBtn'].Text = '3. Publish to Kitsu'
        update_render_review_publish_btns()

    # ── Wiring ────────────────────────────────────────────────────────────────
    def on_close(ev):
        disp.ExitLoop()

    def on_publish_win_close(ev):
        pwin.Hide()

    def on_click(ev):
        w = ev['who']
        if   w == 'LoginBtn':       do_login()
        elif w == 'OpenPublishBtn': pwin.Show()

    def on_pub_click(ev):
        w = ev['who']
        if   w == 'RenderBtn':  do_render()
        elif w == 'ReviewBtn':  do_review()
        elif w == 'PublishBtn': do_publish()

    win.On.KitsuWin.Close                  = on_close
    win.On.LoginBtn.Clicked                = on_click
    win.On.OpenPublishBtn.Clicked          = on_click
    win.On.BrProj.CurrentIndexChanged      = on_br_combo
    win.On.BrSeq.CurrentIndexChanged       = on_br_combo
    win.On.BrTask.CurrentIndexChanged      = on_br_combo
    win.On.BrTree.ItemClicked              = on_br_tree_click
    win.On.BrVersionTree.ItemClicked       = on_br_version_click
    win.On.BrOpenBtn.Clicked               = on_br_action
    win.On.BrNewBtn.Clicked                = on_br_action
    win.On.BrNewFromCurrentBtn.Clicked     = on_br_action
    win.On.BrImportBtn.Clicked             = on_br_import

    pwin.On.PublishWin.Close                = on_publish_win_close
    pwin.On.RenderBtn.Clicked               = on_pub_click
    pwin.On.ReviewBtn.Clicked               = on_pub_click
    pwin.On.PublishBtn.Clicked              = on_pub_click

    # ── Auto-login ────────────────────────────────────────────────────────────
    if s.get('server') and s.get('email') and credentials.get_password(s.get('email')):
        log('[kitsu] auto-signing in...')
        do_login()

    win.Show()
    disp.RunLoop()
    win.Hide()
    pwin.Hide()
