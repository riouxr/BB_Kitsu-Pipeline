'''Kitsu Publisher UI for DaVinci Resolve - UI-B, built directly on PySide.

Real Qt widgets (QWidget/QVBoxLayout/QPushButton/QTreeWidget/...), the same
approach the Nuke side's ``browser.py`` uses, instead of Fusion's
``UIManager`` wrapper (kept as ``ui_a.py``, no longer developed further -
see its own docstring for why). Laid out close to Nuke's own Kitsu Browser:
a navigation tree on the left (Sequence > Shot > Task, lazily filled in as
branches are expanded, exactly the way Nuke's browser only asks Kitsu for a
shot's tasks once that shot is opened), and a version list with pictures on
the right.

Must be launched as its own process, never through Resolve's Workspace >
Scripts menu - see ``resolve/test_standalone_window.py`` and
``resolve_ops._connect_external`` for why: a second Qt event loop started
inside Fusion's own script host is a long-documented Fusion bug ("PySide
freezes Fusion"). Resolve's Fusion process already runs a Qt QApplication
when reached that way; run standalone instead and there is none yet, so
main() creates one and blocks on ``exec()`` itself.
'''
import os
import re

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    _PYSIDE = 'PySide6'
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
        _PYSIDE = 'PySide2'
    except ImportError:
        QtCore = QtGui = QtWidgets = None
        _PYSIDE = None

from BB_core import credentials, settings
from BB_core.kitsu import AuthError, KitsuClient, KitsuError, explain

from . import publish, resolve_ops, session, thumbnails

state = session.state

available = _PYSIDE is not None
import_error = '' if available else 'neither PySide6 nor PySide2 is importable here'

LBL        = 'color:#8a97b3;font-size:10px;font-weight:bold;'
BTN_BLUE   = ('QPushButton{background:#3a6ff7;color:#fff;font-weight:bold;'
             'padding:5px 10px;border-radius:4px;}'
             'QPushButton:disabled{background:#2a2f3d;color:#565f70;}')
BTN_GREEN  = ('QPushButton{background:#2e8b57;color:#fff;font-weight:bold;'
             'padding:5px 10px;border-radius:4px;}'
             'QPushButton:disabled{background:#2a2f3d;color:#565f70;}')
BTN_ORANGE = ('QPushButton{background:#c76b1a;color:#fff;font-weight:bold;'
             'padding:5px 10px;border-radius:4px;}'
             'QPushButton:disabled{background:#2a2f3d;color:#565f70;}')
BTN_PURPLE = ('QPushButton{background:#7a4fd6;color:#fff;font-weight:bold;'
             'padding:5px 10px;border-radius:4px;}'
             'QPushButton:disabled{background:#2a2f3d;color:#565f70;}')
BTN_PUBLISH = ('QPushButton{background:#3a6ff7;color:#fff;font-weight:bold;'
              'font-size:13px;padding:8px;border-radius:5px;}'
              'QPushButton:disabled{background:#2a2f3d;color:#565f70;}')
TAB_ACTIVE   = ('QPushButton{background:#3a6ff7;color:#fff;font-weight:bold;'
               'padding:6px;border-radius:4px 4px 0 0;}')
TAB_INACTIVE = ('QPushButton{background:#252a38;color:#8a97b3;'
                'padding:6px;border-radius:4px 4px 0 0;}')

DEFAULT_TASK_NAME = 'Color Grading'

TAB_BROWSE = 0
TAB_PUBLISH = 1

# QTreeWidgetItem.setData roles, kept as plain dict values under a single
# custom role rather than several - one lookup, one place a node's kind can
# drift out of sync with what it actually holds.
_ROLE = QtCore.Qt.UserRole if QtCore else None


def _apply_dark_theme(app):
    '''A dark QPalette on the QApplication - cascades to every widget by
    default, rather than hand-styling each one the way the Fusion build had
    to. Individual buttons still layer their own accent colour on top.
    '''
    app.setStyle('Fusion')
    pal = QtGui.QPalette()
    window = QtGui.QColor('#1c1e26')
    base = QtGui.QColor('#15171e')
    alt_base = QtGui.QColor('#20222c')
    text = QtGui.QColor('#d6dae6')
    disabled_text = QtGui.QColor('#5a6178')
    highlight = QtGui.QColor('#3a6ff7')

    pal.setColor(QtGui.QPalette.Window, window)
    pal.setColor(QtGui.QPalette.WindowText, text)
    pal.setColor(QtGui.QPalette.Base, base)
    pal.setColor(QtGui.QPalette.AlternateBase, alt_base)
    pal.setColor(QtGui.QPalette.ToolTipBase, window)
    pal.setColor(QtGui.QPalette.ToolTipText, text)
    pal.setColor(QtGui.QPalette.Text, text)
    pal.setColor(QtGui.QPalette.Button, alt_base)
    pal.setColor(QtGui.QPalette.ButtonText, text)
    pal.setColor(QtGui.QPalette.BrightText, QtGui.QColor('#ff5a5a'))
    pal.setColor(QtGui.QPalette.Link, highlight)
    pal.setColor(QtGui.QPalette.Highlight, highlight)
    pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor('#ffffff'))
    pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, disabled_text)
    pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.WindowText, disabled_text)
    pal.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, disabled_text)
    app.setPalette(pal)


def _parent():
    '''Resolve's main window, when this happens to share its process - it
    normally will not, run standalone, so None (a plain top-level window) is
    the common case.
    '''
    try:
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if widget.inherits('QMainWindow') and widget.parent() is None:
                return widget
    except Exception:
        pass
    return None


def _row(label_text, widget, label_width=62):
    box = QtWidgets.QHBoxLayout()
    box.setSpacing(6)
    lbl = QtWidgets.QLabel(label_text)
    lbl.setStyleSheet(LBL)
    lbl.setFixedWidth(label_width)
    box.addWidget(lbl)
    box.addWidget(widget)
    return box


class KitsuPublisher:
    '''Built as a plain object holding the dialog, not a QDialog subclass -
    a reloaded module should not leave a stale class behind (the same
    reasoning Nuke's own Browser class uses).
    '''

    def __init__(self):
        self.ui_state = {
            'all_shots': [],                # every shot in the current project
            'selected_sequence': None,
            'selected_shot': None,
            'selected_task': None,
            'version_mode': None,           # 'project' | 'render'
            'selected_version': None,
            'render_versions': [],
            'current_task_id': None,
            'version': 1,
            'project_name': None,
            'last_render_path': None,
            'working_timeline_name': None,
            'reviewed': False,
            'published': False,
        }
        state.reset()
        self._build_window()
        self._wire()
        self._auto_login()

    # ── Construction ──────────────────────────────────────────────────────

    def _build_window(self):
        s = settings.load()
        win = QtWidgets.QDialog(_parent())
        win.setWindowTitle('Kitsu Publisher')
        win.resize(820, 620)
        self.win = win

        root = QtWidgets.QVBoxLayout(win)
        root.setSpacing(6)

        # -- account, collapsible --------------------------------------------
        acct_header = QtWidgets.QHBoxLayout()
        self.acct_toggle = QtWidgets.QToolButton()
        self.acct_toggle.setArrowType(QtCore.Qt.DownArrow)
        self.acct_toggle.setFixedSize(18, 18)
        self.acct_toggle.setStyleSheet('QToolButton{border:none;}')
        acct_lbl = QtWidgets.QLabel('ACCOUNT')
        acct_lbl.setStyleSheet(LBL)
        acct_header.addWidget(self.acct_toggle)
        acct_header.addWidget(acct_lbl)
        acct_header.addStretch(1)
        root.addLayout(acct_header)

        self.acct_group = QtWidgets.QWidget()
        acct_layout = QtWidgets.QVBoxLayout(self.acct_group)
        acct_layout.setContentsMargins(0, 0, 0, 0)
        acct_layout.setSpacing(4)

        self.server = QtWidgets.QLineEdit(s.get('server', ''))
        self.server.setPlaceholderText('http://192.168.x.x')
        self.email = QtWidgets.QLineEdit(s.get('email', ''))
        self.email.setPlaceholderText('artist@studio.com')
        self.pwd = QtWidgets.QLineEdit()
        self.pwd.setEchoMode(QtWidgets.QLineEdit.Password)
        self.pwd.setPlaceholderText('password')
        self.login_btn = QtWidgets.QPushButton('Sign in')
        self.login_btn.setFixedHeight(28)
        self.login_btn.setStyleSheet(BTN_BLUE)

        acct_layout.addLayout(_row('SERVER', self.server))
        acct_layout.addLayout(_row('EMAIL', self.email))
        acct_layout.addLayout(_row('PASSWORD', self.pwd))
        acct_layout.addWidget(self.login_btn)
        root.addWidget(self.acct_group)

        self.status_lbl = QtWidgets.QLabel('')
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet('color:#e05a6a;font-size:11px;')
        root.addWidget(self.status_lbl)

        # -- tab buttons -------------------------------------------------------
        tabs = QtWidgets.QHBoxLayout()
        tabs.setSpacing(2)
        self.tab_browse_btn = QtWidgets.QPushButton('Browse')
        self.tab_publish_btn = QtWidgets.QPushButton('Publish')
        for btn in (self.tab_browse_btn, self.tab_publish_btn):
            btn.setFixedHeight(28)
            tabs.addWidget(btn)
        root.addLayout(tabs)

        # -- browse tab ----------------------------------------------------
        self.browse_tab = QtWidgets.QWidget()
        browse_split = QtWidgets.QHBoxLayout(self.browse_tab)
        browse_split.setContentsMargins(0, 0, 0, 0)

        left = QtWidgets.QWidget()
        left.setFixedWidth(230)
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.proj_combo = QtWidgets.QComboBox()
        left_layout.addLayout(_row('PROJECT', self.proj_combo, label_width=52))

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAlternatingRowColors(True)
        left_layout.addWidget(self.tree, 1)

        self.thumb = QtWidgets.QLabel('')
        self.thumb.setFixedHeight(90)
        self.thumb.setAlignment(QtCore.Qt.AlignCenter)
        self.thumb.setStyleSheet('background:#0e0f14;border-radius:4px;')
        left_layout.addWidget(self.thumb)

        self.facts_lbl = QtWidgets.QLabel('')
        self.facts_lbl.setStyleSheet('color:#8a97b3;font-size:10px;')
        left_layout.addWidget(self.facts_lbl)

        self.task_count_lbl = QtWidgets.QLabel('')
        self.task_count_lbl.setStyleSheet('color:#8a97b3;font-size:10px;')
        left_layout.addWidget(self.task_count_lbl)

        browse_split.addWidget(left)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # On by default - same reasoning as Nuke's browser: once a task has
        # a flagged master, every WIP render in between is noise for the
        # purpose of picking what to comp against.
        self.master_only_chk = QtWidgets.QCheckBox('Master only')
        self.master_only_chk.setChecked(bool(settings.get('master_only', True)))
        right_layout.addWidget(self.master_only_chk)

        self.version_tree = QtWidgets.QTreeWidget()
        self.version_tree.setHeaderLabels(['Version', 'Detail'])
        self.version_tree.setColumnWidth(0, 70)
        self.version_tree.setIconSize(QtCore.QSize(48, 27))
        self.version_tree.setRootIsDecorated(False)
        self.version_tree.setAlternatingRowColors(True)
        right_layout.addWidget(self.version_tree, 1)

        self.browse_status_lbl = QtWidgets.QLabel('')
        self.browse_status_lbl.setWordWrap(True)
        self.browse_status_lbl.setStyleSheet('color:#8a97b3;font-size:11px;')
        right_layout.addWidget(self.browse_status_lbl)

        actions = QtWidgets.QHBoxLayout()
        self.open_btn = QtWidgets.QPushButton('Open')
        self.new_btn = QtWidgets.QPushButton('New Version')
        self.new_from_current_btn = QtWidgets.QPushButton('New From Current')
        for btn, style in ((self.open_btn, BTN_BLUE), (self.new_btn, BTN_GREEN),
                          (self.new_from_current_btn, BTN_ORANGE)):
            btn.setFixedHeight(30)
            btn.setStyleSheet(style)
            btn.setEnabled(False)
            actions.addWidget(btn)
        right_layout.addLayout(actions)

        self.import_btn = QtWidgets.QPushButton('Import in Media Storage')
        self.import_btn.setFixedHeight(28)
        self.import_btn.setStyleSheet(BTN_PURPLE)
        self.import_btn.setEnabled(False)
        right_layout.addWidget(self.import_btn)

        browse_split.addWidget(right, 1)
        root.addWidget(self.browse_tab, 1)

        # -- publish tab -----------------------------------------------------
        self.publish_tab = QtWidgets.QWidget()
        pub_layout = QtWidgets.QVBoxLayout(self.publish_tab)
        pub_layout.setContentsMargins(0, 0, 0, 0)

        self.pub_task_lbl = QtWidgets.QLabel('TASK: (none)')
        self.pub_task_lbl.setStyleSheet('color:#c8a84b;font-size:11px;font-weight:bold;')
        pub_layout.addWidget(self.pub_task_lbl)

        self.task_status = QtWidgets.QComboBox()
        self.task_status.setEnabled(False)
        status_lbl = QtWidgets.QLabel('STATUS'); status_lbl.setStyleSheet(LBL)
        pub_layout.addWidget(status_lbl)
        pub_layout.addWidget(self.task_status)

        self.preset = QtWidgets.QComboBox()
        preset_lbl = QtWidgets.QLabel('RENDER PRESET'); preset_lbl.setStyleSheet(LBL)
        pub_layout.addWidget(preset_lbl)
        pub_layout.addWidget(self.preset)

        self.comment = QtWidgets.QTextEdit()
        self.comment.setFixedHeight(60)
        comment_lbl = QtWidgets.QLabel('COMMENT'); comment_lbl.setStyleSheet(LBL)
        pub_layout.addWidget(comment_lbl)
        pub_layout.addWidget(self.comment)

        step_row = QtWidgets.QHBoxLayout()
        self.render_btn = QtWidgets.QPushButton('1. Render')
        self.review_btn = QtWidgets.QPushButton('2. Review in Resolve')
        for btn, style in ((self.render_btn, BTN_GREEN), (self.review_btn, BTN_ORANGE)):
            btn.setFixedHeight(34)
            btn.setStyleSheet(style)
            btn.setEnabled(False)
            step_row.addWidget(btn)
        pub_layout.addLayout(step_row)

        self.publish_btn = QtWidgets.QPushButton('3. Publish to Kitsu')
        self.publish_btn.setFixedHeight(38)
        self.publish_btn.setStyleSheet(BTN_PUBLISH)
        self.publish_btn.setEnabled(False)
        pub_layout.addWidget(self.publish_btn)

        self.pub_steps_lbl = QtWidgets.QLabel('')
        self.pub_steps_lbl.setWordWrap(True)
        self.pub_steps_lbl.setStyleSheet('color:#8a97b3;font-size:11px;')
        pub_layout.addWidget(self.pub_steps_lbl)
        pub_layout.addStretch(1)

        root.addWidget(self.publish_tab, 1)

        self._presets()
        self._show_tab(TAB_BROWSE)

    def _wire(self):
        self.win.finished.connect(self._on_closed)
        self.acct_toggle.clicked.connect(self._toggle_account)
        self.login_btn.clicked.connect(self.do_login)
        self.tab_browse_btn.clicked.connect(lambda: self._show_tab(TAB_BROWSE))
        self.tab_publish_btn.clicked.connect(lambda: self._show_tab(TAB_PUBLISH))

        self.proj_combo.currentIndexChanged.connect(self._load_tree)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.version_tree.itemClicked.connect(self._on_version_clicked)

        self.open_btn.clicked.connect(self.on_open)
        self.new_btn.clicked.connect(self.on_new_version)
        self.master_only_chk.stateChanged.connect(self._on_master_only_changed)
        self.new_from_current_btn.clicked.connect(self.on_new_from_current)
        self.import_btn.clicked.connect(self.on_import)

        self.render_btn.clicked.connect(self.do_render)
        self.review_btn.clicked.connect(self.do_review)
        self.publish_btn.clicked.connect(self.do_publish)

    def show(self):
        self.win.show()

    def _on_closed(self, _result):
        app = QtWidgets.QApplication.instance()
        if app:
            app.quit()

    # ── Tabs / account collapse ──────────────────────────────────────────

    def _show_tab(self, idx):
        self.browse_tab.setVisible(idx == TAB_BROWSE)
        self.publish_tab.setVisible(idx == TAB_PUBLISH)
        self.tab_browse_btn.setStyleSheet(TAB_ACTIVE if idx == TAB_BROWSE else TAB_INACTIVE)
        self.tab_publish_btn.setStyleSheet(TAB_ACTIVE if idx == TAB_PUBLISH else TAB_INACTIVE)

    def _toggle_account(self):
        self._set_account_collapsed(self.acct_group.isVisible())

    def _set_account_collapsed(self, collapsed):
        self.acct_group.setVisible(not collapsed)
        self.acct_toggle.setArrowType(QtCore.Qt.RightArrow if collapsed else QtCore.Qt.DownArrow)

    # ── Generic helpers ──────────────────────────────────────────────────

    def log(self, msg):
        try:
            print(msg)
        except UnicodeEncodeError:
            # A console whose codepage cannot encode the checkmarks/arrows
            # used in these messages (the launcher reconfigures stdout to
            # avoid this, but anything importing this module directly does
            # not get that for free) - never let a log line crash the run.
            print(msg.encode('ascii', 'replace').decode('ascii'))
        app = QtWidgets.QApplication.instance()
        if app:
            app.processEvents()

    def setstatus(self, msg, ok=False):
        self.status_lbl.setText(msg)
        self.status_lbl.setStyleSheet(
            'color:#4fbb6a;font-size:11px;' if ok else 'color:#e05a6a;font-size:11px;')

    def _fill(self, combo, lst, key='name', placeholder='select'):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(placeholder)
        for x in lst:
            combo.addItem(x[key])
        combo.setEnabled(bool(lst))
        combo.blockSignals(False)

    def _presets(self):
        try:
            p = resolve_ops.get_current_project().GetRenderPresetList() or []
        except Exception:
            p = []
        self.preset.clear()
        self.preset.addItem('H.264 default')
        for x in p:
            self.preset.addItem(x)

    # ── Assigned task / project ──────────────────────────────────────────

    def current_task(self):
        tid = self.ui_state['current_task_id']
        if not tid:
            return None
        return next((t for t in state.shot_tasks if t.get('id') == tid), None)

    def refresh_statuses(self):
        self._fill(self.task_status, state.project_statuses(), placeholder='no change')
        self.task_status.setEnabled(True)
        for i, x in enumerate(state.project_statuses()):
            if x.get('short_name', '').upper() in ('WFA', 'WAITING FOR APPROVAL'):
                self.task_status.setCurrentIndex(i + 1)
                break

    def update_publish_task_label(self):
        task = self.current_task()
        self.pub_task_lbl.setText(
            'TASK: ' + state.task_type_name(task.get('task_type_id'))
            if task else 'TASK: (none)')

    def update_render_review_publish_btns(self):
        ready = bool(state.client and self.ui_state['project_name']
                    and state.shot and self.current_task())
        self.render_btn.setEnabled(ready)
        has_render = ready and bool(self.ui_state['last_render_path'])
        self.review_btn.setEnabled(has_render)
        self.publish_btn.setEnabled(has_render)
        self._update_steps_label()

    def _update_steps_label(self):
        rendered = bool(self.ui_state['last_render_path'])
        reviewed = self.ui_state['reviewed']
        published = self.ui_state['published']

        def mark(done, label):
            return ('✓ ' if done else '○ ') + label

        self.pub_steps_lbl.setText('   '.join([
            mark(rendered, 'Rendered'), mark(reviewed, 'Reviewed'), mark(published, 'Published'),
        ]))
        self.pub_steps_lbl.setStyleSheet(
            'color:#4fbb6a;font-size:11px;' if published else 'color:#8a97b3;font-size:11px;')

    def set_assigned(self, name):
        self.ui_state['project_name'] = name
        self.ui_state['version'] = resolve_ops.version_from_name(name)
        self.ui_state['last_render_path'] = None
        self.ui_state['working_timeline_name'] = None
        self.ui_state['reviewed'] = False
        self.ui_state['published'] = False
        self.render_btn.setText('1. Render')
        self._remember_working_timeline()
        self.update_publish_task_label()
        self.update_render_review_publish_btns()

    def _remember_working_timeline(self):
        try:
            project = resolve_ops.get_current_project()
            current = project.GetCurrentTimeline() if project else None
            name = current.GetName() if current else None
        except Exception:
            name = None
        if name and name != resolve_ops.REVIEW_TIMELINE_NAME:
            self.ui_state['working_timeline_name'] = name

    def _restore_working_timeline(self, project):
        name = self.ui_state.get('working_timeline_name')
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
                        self.log('[resolve] back on your working timeline (%s)' % name)
                    else:
                        self.log('[resolve] ERROR: Resolve refused to switch back to "%s"' % name)
                    return
            self.log('[resolve] ERROR: working timeline "%s" no longer exists' % name)
        except Exception as e:
            self.log('[resolve] could not restore working timeline: %s' % e)

    def _load_tasks_for_shot(self, shot):
        self.log('[kitsu] loading tasks...')
        try:
            tasks = state.client.tasks_for_shot(shot['id'])
        except KitsuError as e:
            self.log('[kitsu] error: ' + str(e)); return
        state.shot_tasks = state.sort_tasks(tasks)
        self.update_publish_task_label()
        self.update_render_review_publish_btns()

    # ── Login ─────────────────────────────────────────────────────────────

    def do_login(self):
        server = self.server.text().strip()
        email = self.email.text().strip()
        typed = self.pwd.text().strip()
        pwd = typed or (credentials.get_password(email) if email else None)
        if not (server and email and pwd):
            self.setstatus('Fill in the server, email and password.'); return
        self.setstatus('Signing in...')
        try:
            client = KitsuClient(server, verify=not settings.get('allow_insecure_tls', False))
            client.log_in(email, pwd)
        except (AuthError, KitsuError) as e:
            self.setstatus(explain(e, server, email)); return
        except Exception as e:
            import traceback
            self.setstatus('Login failed: ' + str(e))
            self.log('[login error]\n' + traceback.format_exc()); return

        state.client = client
        self.setstatus('Connected ✓', ok=True)
        settings.save({'server': server, 'email': email})
        if typed and credentials.available():
            credentials.set_password(email, typed)
        self.pwd.setText('')
        self._set_account_collapsed(True)
        self._load_initial()

    def _load_initial(self):
        c = state.client
        self.log('[kitsu] loading data...')
        try:
            state.projects = c.open_projects()
            state.task_types = {t['id']: t for t in c.task_types()}
            state.departments = {d['id']: d for d in c.departments()}
            state.statuses = c.task_statuses()
        except KitsuError as e:
            self.log('[kitsu] error: ' + str(e)); return
        state.load_departments_filter()
        self._fill(self.task_status, state.statuses, placeholder='no change')
        self.task_status.setEnabled(True)
        self.log('[kitsu] %d project(s) loaded' % len(state.projects))

        self._fill(self.proj_combo, state.projects, placeholder='select project')
        saved_p = settings.get('last_project')
        for i, p in enumerate(state.projects):
            if p['id'] == saved_p:
                self.proj_combo.setCurrentIndex(i + 1)
                break

        self._detect_current_project()

    def _auto_login(self):
        s = settings.load()
        if s.get('server') and s.get('email') and credentials.get_password(s.get('email')):
            self.log('[kitsu] auto-signing in...')
            self.do_login()

    # ── Auto-detect ───────────────────────────────────────────────────────

    def _detect_current_project(self):
        try:
            curr_name = resolve_ops.get_current_project().GetName()
        except Exception:
            return
        m = re.match(r'^(.+)_v(\d+)$', curr_name, re.IGNORECASE)
        if not m:
            self.log("[detect] '%s' doesn't look like a Kitsu project name" % curr_name)
            return
        base = m.group(1)
        ver = int(m.group(2))
        parts = base.rsplit('_', 3)
        if len(parts) != 4:
            self.log("[detect] can't parse base '%s' into proj/seq/shot/task" % base); return
        proj_san, seq_san, shot_san, task_san = parts

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
                        self.ui_state['selected_sequence'] = seq
                        self.ui_state['selected_shot'] = shot
                        self.ui_state['selected_task'] = task
                        self.ui_state['version'] = ver
                        self.ui_state['current_task_id'] = task['id']
                        settings.save({'last_task': task['id']})
                        self.refresh_statuses()
                        self.set_assigned(curr_name)
                        self._load_tasks_for_shot(shot)
                        self.log("[detect] ✓ matched '%s' → %s / %s / %s / %s"
                                % (curr_name, proj['name'], seq['name'], shot['name'], task_name))
                        return
        self.log("[detect] no Kitsu match found for '%s'" % curr_name)

    # ── Tree (Sequence > Shot > Task, lazily filled) ─────────────────────

    def _current_project_dict(self):
        idx = self.proj_combo.currentIndex() - 1
        return state.projects[idx] if 0 <= idx < len(state.projects) else None

    def _load_tree(self):
        self.tree.clear()
        self._clear_selection()
        proj = self._current_project_dict()
        if not proj:
            return
        try:
            sequences = state.client.sequences(proj['id'])
            self.ui_state['all_shots'] = state.client.shots_for_project(proj['id'])
        except KitsuError as e:
            self.log('[browse] ' + str(e)); return

        for seq in sequences:
            item = QtWidgets.QTreeWidgetItem([seq.get('name', seq['id'])])
            item.setData(0, _ROLE, {'kind': 'sequence', 'sequence': seq, 'loaded': False})
            item.addChild(QtWidgets.QTreeWidgetItem(['...']))  # placeholder for the expand arrow
            self.tree.addTopLevelItem(item)

    def _on_item_expanded(self, item):
        data = item.data(0, _ROLE)
        if not data or data.get('loaded'):
            return

        if data['kind'] == 'sequence':
            seq = data['sequence']
            shots = [s for s in self.ui_state['all_shots'] if s.get('parent_id') == seq['id']]
            item.takeChildren()
            for shot in shots:
                child = QtWidgets.QTreeWidgetItem([shot.get('name', shot['id'])])
                child.setData(0, _ROLE, {'kind': 'shot', 'sequence': seq, 'shot': shot, 'loaded': False})
                child.addChild(QtWidgets.QTreeWidgetItem(['...']))
                item.addChild(child)
            data['loaded'] = True

        elif data['kind'] == 'shot':
            shot = data['shot']
            seq = data['sequence']
            item.takeChildren()
            try:
                tasks = state.client.tasks_for_shot(shot['id'])
            except KitsuError as e:
                self.log('[browse] ' + str(e)); return
            tasks = state.sort_tasks(tasks)
            for t in tasks:
                task_name = state.task_type_name(t.get('task_type_id'))
                label = task_name + ('  (renders)' if not state.authors(t) else '')
                child = QtWidgets.QTreeWidgetItem([label])
                child.setData(0, _ROLE, {'kind': 'task', 'sequence': seq, 'shot': shot, 'task': t})
                item.addChild(child)
            data['loaded'] = True
            self._update_shot_facts(shot, tasks)

    def _on_item_clicked(self, item, _column):
        data = item.data(0, _ROLE)
        if not data:
            return
        if data['kind'] != 'task':
            item.setExpanded(not item.isExpanded())
            return

        self.ui_state['selected_sequence'] = data['sequence']
        self.ui_state['selected_shot'] = data['shot']
        self.ui_state['selected_task'] = data['task']

        pm = thumbnails.qpixmap(state.client, data['shot'], width=200)
        self.thumb.setPixmap(pm if pm else QtGui.QPixmap())
        self._refresh_versions()

    def _update_shot_facts(self, shot, tasks):
        info = (shot or {}).get('data') or {}
        frame_in = info.get('frame_in') or shot.get('frame_in')
        frame_out = info.get('frame_out') or shot.get('frame_out')
        fps = info.get('fps')
        proj = self._current_project_dict() or {}
        resolution = proj.get('resolution', '')
        bits = []
        if frame_in and frame_out:
            bits.append('%s-%s' % (frame_in, frame_out))
        if fps:
            bits.append('%s fps' % fps)
        if resolution:
            bits.append(str(resolution))
        self.facts_lbl.setText(' · '.join(bits))

        authored = [t for t in tasks if state.authors(t)]
        self.task_count_lbl.setText('%d task(s) for Resolve' % len(authored))

    def _clear_selection(self):
        self.ui_state['selected_sequence'] = None
        self.ui_state['selected_shot'] = None
        self.ui_state['selected_task'] = None
        self.thumb.setPixmap(QtGui.QPixmap())
        self.facts_lbl.setText('')
        self.task_count_lbl.setText('')
        self._reset_version_ui()

    # ── Versions ──────────────────────────────────────────────────────────

    def _base_for_selection(self):
        seq = self.ui_state['selected_sequence']
        shot = self.ui_state['selected_shot']
        task = self.ui_state['selected_task']
        proj = self._current_project_dict()
        if not (seq and shot and task and proj):
            return None
        task_name = state.task_type_name(task.get('task_type_id'))
        return resolve_ops.build_project_base(proj['name'], seq['name'], shot['name'], task_name)

    def _reset_version_ui(self):
        self.version_tree.clear()
        self.browse_status_lbl.setText('')
        for btn in (self.open_btn, self.new_btn, self.new_from_current_btn, self.import_btn):
            btn.setEnabled(False)
        self.ui_state['version_mode'] = None
        self.ui_state['selected_version'] = None
        self.ui_state['render_versions'] = []

    def _on_master_only_changed(self, _state=0):
        settings.set('master_only', self.master_only_chk.isChecked())
        self._refresh_versions()

    def _refresh_versions(self):
        self._reset_version_ui()
        base = self._base_for_selection()
        seq = self.ui_state['selected_sequence']
        shot = self.ui_state['selected_shot']
        task = self.ui_state['selected_task']
        proj = self._current_project_dict()
        if not base:
            return

        icon = QtGui.QIcon(self.thumb.pixmap()) if self.thumb.pixmap() else QtGui.QIcon()

        # Master only means something for a rendered sequence - which one is
        # the comp-against version - and nothing for Resolve's own project
        # versions, where every version is just a project, not a flagged
        # deliverable.
        self.master_only_chk.setVisible(not state.authors(task))

        if state.authors(task):
            self.ui_state['version_mode'] = 'project'
            existing = resolve_ops.get_all_resolve_project_names()
            matches = resolve_ops.matching_versions(base, existing)
            for version, name in reversed(matches):
                item = QtWidgets.QTreeWidgetItem(['v%03d' % version, name])
                item.setIcon(0, icon)
                self.version_tree.addTopLevelItem(item)

            self.browse_status_lbl.setText(
                '%d Resolve project version(s) — click one to Open' % len(matches)
                if matches else 'No Resolve project yet for this shot + task')
            self.new_btn.setEnabled(True)
            try:
                has_current = bool(resolve_ops.get_current_project())
            except Exception:
                has_current = False
            self.new_from_current_btn.setEnabled(has_current)

        elif self.master_only_chk.isChecked():
            self.ui_state['version_mode'] = 'render'
            try:
                frames = publish.master_frames_for(proj, seq, shot, task)
            except Exception as e:
                self.browse_status_lbl.setText('Could not check Master: %s' % e)
                return

            renders = [(0, frames[0], frames[1], frames[2])] if frames else []
            self.ui_state['render_versions'] = renders
            if frames:
                master_version = None
                if state.client and task:
                    from BB_core import master
                    master_version = master.current_master(state.client, task['id'])
                name = 'Master (v%03d)' % master_version if master_version else 'Master'
                item = QtWidgets.QTreeWidgetItem(
                    [name, '%d frame(s)' % (frames[2] - frames[1] + 1)])
                item.setIcon(0, icon)
                self.version_tree.addTopLevelItem(item)
                self.browse_status_lbl.setText('Master — click to Import')
            else:
                self.browse_status_lbl.setText('no master flagged yet for this task')

        else:
            self.ui_state['version_mode'] = 'render'
            try:
                renders = publish.render_versions_for(proj, seq, shot, task)
            except Exception as e:
                self.browse_status_lbl.setText('Could not check for renders: %s' % e)
                return

            self.ui_state['render_versions'] = renders
            for version, _pattern, first, last in reversed(renders):
                item = QtWidgets.QTreeWidgetItem(
                    ['v%03d' % version, '%d frame(s)' % (last - first + 1)])
                item.setIcon(0, icon)
                self.version_tree.addTopLevelItem(item)

            if renders:
                self.browse_status_lbl.setText(
                    '%d rendered version(s) — click one to Import' % len(renders))
            else:
                try:
                    folder = publish.render_folder_for(proj, seq, shot, task, 1)
                    looked_in = os.path.dirname(folder)
                except Exception:
                    looked_in = '(could not resolve a path)'
                self.browse_status_lbl.setText(
                    'No rendered sequence found. Looked in:\n' + looked_in)

    def _on_version_clicked(self, item, _column):
        text = item.text(0)
        if text.startswith('Master'):
            # A sentinel, not a number - Master has no version of its own to
            # reconstruct a path from, only its own already-known folder.
            self.ui_state['selected_version'] = 'master'
        else:
            try:
                self.ui_state['selected_version'] = int(text.lstrip('vV'))
            except (ValueError, AttributeError):
                return
        if self.ui_state['version_mode'] == 'project':
            self.open_btn.setEnabled(True)
        elif self.ui_state['version_mode'] == 'render':
            self.import_btn.setEnabled(True)

    # ── Browse actions ────────────────────────────────────────────────────

    def _finish(self, proj, seq, shot, task, name):
        state.project, state.sequence, state.shot = proj, seq, shot
        self.ui_state['current_task_id'] = task['id']
        settings.save({'last_project': proj['id'], 'last_sequence': seq['id'],
                       'last_shot': shot['id'], 'last_task': task['id']})
        self.refresh_statuses()
        self.set_assigned(name)
        self._load_tasks_for_shot(shot)
        self._show_tab(TAB_PUBLISH)

    def on_open(self):
        base = self._base_for_selection()
        ver = self.ui_state['selected_version']
        if not base or not ver: return
        name = '%s_v%03d' % (base, ver)
        self.log("[resolve] opening '%s'..." % name)
        pm = resolve_ops.get_project_manager()
        if not pm.LoadProject(name):
            self.log("[resolve] ERROR: could not open '%s'" % name); return
        self.log("[resolve] '%s' opened ✓" % name)
        self.ui_state['version'] = ver
        proj = self._current_project_dict()
        self._finish(proj, self.ui_state['selected_sequence'], self.ui_state['selected_shot'],
                    self.ui_state['selected_task'], name)

    def on_new_version(self):
        base = self._base_for_selection()
        if not base: return
        existing = resolve_ops.get_all_resolve_project_names()
        name, ver = resolve_ops.next_version_name(base, existing)
        self.log("[resolve] creating '%s'..." % name)
        pm = resolve_ops.get_project_manager()
        if not pm.CreateProject(name):
            self.log("[resolve] ERROR: could not create '%s'" % name); return
        self.log("[resolve] '%s' created & opened ✓" % name)
        self.ui_state['version'] = ver
        proj = self._current_project_dict()
        self._finish(proj, self.ui_state['selected_sequence'], self.ui_state['selected_shot'],
                    self.ui_state['selected_task'], name)

    def on_new_from_current(self):
        base = self._base_for_selection()
        if not base: return
        pm = resolve_ops.get_project_manager()
        try:
            src_proj = pm.GetCurrentProject()
            src = src_proj.GetName() if src_proj else None
        except Exception:
            src = None
        if not src:
            self.log('[resolve] ERROR: no project currently open to copy from'); return
        existing = resolve_ops.get_all_resolve_project_names()
        name, ver = resolve_ops.next_version_name(base, existing)
        self.log("[resolve] copying '%s'  →  '%s'..." % (src, name))
        if not resolve_ops.copy_resolve_project(src, name, log=self.log):
            self.log('[resolve] ERROR: project copy failed'); return
        self.log("[resolve] '%s' ready ✓  ('%s' untouched)" % (name, src))
        self.ui_state['version'] = ver
        proj = self._current_project_dict()
        self._finish(proj, self.ui_state['selected_sequence'], self.ui_state['selected_shot'],
                    self.ui_state['selected_task'], name)

    def on_import(self):
        proj = self._current_project_dict()
        seq = self.ui_state['selected_sequence']
        shot = self.ui_state['selected_shot']
        task = self.ui_state['selected_task']
        version = self.ui_state['selected_version']
        if not (proj and seq and shot and task and version):
            return

        self.import_btn.setEnabled(False)
        self.import_btn.setText('Importing...')
        try:
            folder = (publish.master_dir_for(proj, seq, shot, task) if version == 'master'
                     else publish.render_folder_for(proj, seq, shot, task, version))
            self.log('[import] %s' % folder)
            resolve_ops.import_sequence_folder(folder, log=self.log)
            self.log('[import] done ✓')
        except Exception as e:
            self.log('[error] ' + str(e))
        self.import_btn.setText('Import in Media Storage')
        self.import_btn.setEnabled(True)

    # ── Render / Review / Publish ────────────────────────────────────────

    def do_render(self):
        task = self.current_task()
        if not task:
            self.log('[error] no task assigned - use Browse first'); return
        pi = self.preset.currentIndex()
        preset = self.preset.itemText(pi) if pi > 0 else None

        def on_progress(pct):
            self.render_btn.setText('Rendering... %d%%' % int(pct))
            app = QtWidgets.QApplication.instance()
            if app:
                app.processEvents()

        self.render_btn.setEnabled(False)
        self.review_btn.setEnabled(False)
        self.publish_btn.setEnabled(False)
        self.ui_state['last_render_path'] = None
        self.ui_state['reviewed'] = False
        self.ui_state['published'] = False
        project = resolve_ops.get_current_project()

        self._remember_working_timeline()
        self._restore_working_timeline(project)

        try:
            resolve_ops.clear_review_timeline(project, log=self.log)
        except Exception as e:
            self.log('[review] could not clear the old review timeline: %s' % e)

        try:
            path = publish.render(project, task, self.ui_state['version'], preset,
                                  log=self.log, on_progress=on_progress)
            self.ui_state['last_render_path'] = path
            self.log('[render] done ✓ — review it, then publish')
        except Exception as e:
            self.log('[error] ' + str(e))
        self.render_btn.setText('1. Render')
        self.update_render_review_publish_btns()

    def do_review(self):
        path = self.ui_state['last_render_path']
        if not path:
            self.log('[error] render first'); return
        self.review_btn.setEnabled(False)
        self.review_btn.setText('Loading...')
        try:
            resolve_ops.load_for_review(resolve_ops.get_current_project(), path, log=self.log)
            self.ui_state['reviewed'] = True
            self.log('[review] ✓ ready to scrub in Resolve')
        except Exception as e:
            self.log('[error] ' + str(e))
        self.review_btn.setText('2. Review in Resolve')
        self.update_render_review_publish_btns()

    def do_publish(self):
        task = self.current_task()
        path = self.ui_state['last_render_path']
        if not task:
            self.log('[error] no task assigned'); return
        if not path:
            self.log('[error] render (and review) before publishing'); return

        statuses = state.project_statuses()
        idx_st = self.task_status.currentIndex() - 1
        status = statuses[idx_st] if 0 <= idx_st < len(statuses) else None
        comment = self.comment.toPlainText().strip()

        settings.save({'last_task': task['id']})
        self.publish_btn.setEnabled(False)
        self.publish_btn.setText('Publishing...')
        try:
            publish.upload(task, status, comment, path, log=self.log)
            self.ui_state['published'] = True
            self.log('[kitsu] published ✓')
            self._restore_working_timeline(resolve_ops.get_current_project())
        except Exception as e:
            self.log('[error] ' + str(e))
        self.publish_btn.setText('3. Publish to Kitsu')
        self.update_render_review_publish_btns()


_instance = None


def main():
    '''Build and show the UI. Kept alive via the module-level ``_instance`` -
    nothing here blocks, so a local variable would be garbage collected the
    moment this function returned.
    '''
    if not available:
        print('[kitsu] UI-B unavailable: %s' % import_error)
        return None

    resolve = resolve_ops.get_resolve()
    if not resolve:
        print('ERROR: could not connect to Resolve - is it running, with a project open?')
        return None

    app = QtWidgets.QApplication.instance()
    if app is None:
        import sys
        app = QtWidgets.QApplication(sys.argv)
    _apply_dark_theme(app)

    global _instance
    _instance = KitsuPublisher()
    _instance.show()

    app.exec()
    return _instance
