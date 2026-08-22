'''The Kitsu browser, as a Nuke panel.

PySide6, because that is what Nuke 16 ships. Qt is imported inside the
functions that need it so the rest of this package can be exercised by a plain
interpreter with no Qt at all - which is how it gets tested on a machine with
no Nuke licence.

Shots only and compositing tasks only, so the cascade is one level shorter
than Blender's: project, sequence, shot, task, version.
'''
import os

from BB_core import credentials, settings
from BB_core.kitsu import AuthError, KitsuClient, KitsuError, explain

from . import (capture, fetch, publish, review, scripts, session, stamp,
               thumbnails)

state = session.state

TITLE = 'Kitsu Browser'


def _qt():
    from PySide6 import QtCore, QtWidgets
    return QtCore, QtWidgets


def _parent():
    '''Nuke's main window, so the dialog behaves like part of the app.'''
    try:
        from PySide6 import QtWidgets
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if widget.inherits('QMainWindow') and widget.parent() is None:
                return widget
    except Exception:
        pass
    return None


class Browser(object):
    '''Built as a plain object holding a dialog, not a QDialog subclass.

    Subclassing across a reload leaves Nuke holding a stale class object and
    the second call fails in a way that reads like the panel is broken. This
    keeps the Qt objects behind an ordinary Python instance that can be thrown
    away and rebuilt.
    '''

    def __init__(self):
        QtCore, QtWidgets = _qt()

        self.dialog = QtWidgets.QDialog(_parent())
        self.dialog.setWindowTitle(TITLE)
        self.dialog.setMinimumWidth(460)

        layout = QtWidgets.QVBoxLayout(self.dialog)

        self.status = QtWidgets.QLabel('')
        self.status.setWordWrap(True)

        form = QtWidgets.QFormLayout()
        self.project = QtWidgets.QComboBox()
        self.sequence = QtWidgets.QComboBox()
        self.shot = QtWidgets.QComboBox()
        self.task = QtWidgets.QComboBox()
        form.addRow('Project', self.project)
        form.addRow('Sequence', self.sequence)
        form.addRow('Shot', self.shot)
        form.addRow('Task', self.task)
        layout.addLayout(form)

        # The shot's Kitsu thumbnail, so it can be recognised before anything
        # is opened. When there is not one it says so rather than going blank:
        # most shots on a young show have no preview yet, and silence there is
        # indistinguishable from the feature being broken.
        self.thumbnail = QtWidgets.QLabel('')
        self.thumbnail.setAlignment(QtCore.Qt.AlignCenter)
        self.thumbnail.setMinimumHeight(24)
        layout.addWidget(self.thumbnail)

        self.versions = QtWidgets.QListWidget()
        self.versions.setMinimumHeight(120)
        layout.addWidget(self.versions)

        buttons = QtWidgets.QHBoxLayout()
        self.open_button = QtWidgets.QPushButton('Open')
        self.new_button = QtWidgets.QPushButton('New Version')
        self.new_from_button = QtWidgets.QPushButton('New from Current')
        for widget in (self.open_button, self.new_button, self.new_from_button):
            buttons.addWidget(widget)
        layout.addLayout(buttons)

        # Import is its own row: the three above replace what is open, this
        # one adds to it, and a mis-click between those two is expensive.
        self.import_button = QtWidgets.QPushButton('Import into Current Script')
        layout.addWidget(self.import_button)

        layout.addWidget(self.status)

        footer = QtWidgets.QHBoxLayout()
        self.settings_button = QtWidgets.QPushButton('Settings...')
        self.refresh_button = QtWidgets.QPushButton('Refresh')
        footer.addWidget(self.settings_button)
        footer.addStretch(1)
        footer.addWidget(self.refresh_button)
        layout.addLayout(footer)

        self.project.currentIndexChanged.connect(self._on_project)
        self.sequence.currentIndexChanged.connect(self._on_sequence)
        self.shot.currentIndexChanged.connect(self._on_shot)
        self.task.currentIndexChanged.connect(self._on_task)

        self.open_button.clicked.connect(self._open)
        self.new_button.clicked.connect(lambda: self._create(False))
        self.new_from_button.clicked.connect(lambda: self._create(True))
        self.import_button.clicked.connect(self._import)
        self.settings_button.clicked.connect(self._settings)
        self.refresh_button.clicked.connect(self.reload)

        self._loading = False

    # -- filling in -----------------------------------------------------------

    def reload(self):
        '''Reconnect if needed and refill everything from Kitsu.'''
        if not state.connected:
            problem = fetch.connect()
            if problem:
                self._say(problem, error=True)
                return

        self._fill(self.project, state.projects, settings.get('last_project'))

    def _fill(self, combo, rows, remembered=None):
        '''Refill a combo, keeping the id on each item and restoring a choice.'''
        self._loading = True
        try:
            combo.clear()
            for row in rows:
                combo.addItem(row.get('name', '?'), row['id'])
            if remembered:
                index = combo.findData(remembered)
                if index >= 0:
                    combo.setCurrentIndex(index)
        finally:
            self._loading = False

        if combo.count():
            combo.currentIndexChanged.emit(combo.currentIndex())

    def _id(self, combo):
        return combo.currentData()

    # -- the cascade ----------------------------------------------------------

    def _on_project(self, _index=0):
        if self._loading:
            return
        project_id = self._id(self.project)
        if not project_id:
            return
        settings.set('last_project', project_id)
        fetch.project_selected(project_id)
        self._say(state.message, state.is_error)
        self._fill(self.sequence, state.sequences, settings.get('last_sequence'))

    def _on_sequence(self, _index=0):
        if self._loading:
            return
        sequence_id = self._id(self.sequence)
        if not sequence_id:
            return
        settings.set('last_sequence', sequence_id)
        self._fill(self.shot, state.shots_in(sequence_id),
                   settings.get('last_shot'))

    def _on_shot(self, _index=0):
        if self._loading:
            return
        shot_id = self._id(self.shot)
        if not shot_id:
            return
        settings.set('last_shot', shot_id)
        fetch.shot_selected(shot_id)
        self._say(state.message, state.is_error)
        self._show_thumbnail(shot_id)

        tasks = state.comp_tasks()
        rows = [{'id': t['id'],
                 'name': state.task_type_name(t.get('task_type_id', ''))}
                for t in tasks]
        self._fill(self.task, rows, settings.get('last_task'))
        if not rows:
            self.versions.clear()

    def _on_task(self, _index=0):
        if self._loading:
            return
        task_id = self._id(self.task)
        if not task_id:
            return
        settings.set('last_task', task_id)
        self._refresh_versions()

    def _show_thumbnail(self, shot_id):
        '''Draw the shot's Kitsu thumbnail, or say why there is not one.'''
        picture = None
        if state.connected:
            picture = thumbnails.pixmap(state.client, state.shot(shot_id))

        if picture is None:
            self.thumbnail.clear()
            self.thumbnail.setText(
                '<span style="color:#8a8a8a">no thumbnail in Kitsu for this '
                'shot</span>')
            return

        self.thumbnail.setPixmap(picture)

    def _refresh_versions(self):
        self.versions.clear()
        entity_context = self.context()
        if entity_context is None:
            return

        found = fetch.list_versions(entity_context)
        for version, path in reversed(found):
            self.versions.addItem('v%03d    %s' % (version, os.path.basename(str(path))))
        if found:
            self.versions.setCurrentRow(0)
            self.new_button.setText('New v%03d' % (found[-1][0] + 1))
        else:
            self.new_button.setText('Create v001')
            self._say('no script yet for this task')

        self.open_button.setEnabled(bool(found))
        self.import_button.setEnabled(bool(found))

    def context(self):
        return fetch.current_context(self._id(self.project), self._id(self.sequence),
                                     self._id(self.shot), self._id(self.task))

    # -- actions --------------------------------------------------------------

    def _selected_path(self):
        row = self.versions.currentRow()
        found = state.workfiles
        if row < 0 or not found:
            return ''
        # The list is newest first; the store is oldest first.
        return str(list(reversed(found))[row][1])

    def _open(self):
        path = self._selected_path()
        if not path:
            self._say('No version selected', error=True)
            return
        try:
            opened = scripts.open_version(path)
        except scripts.ScriptError as error:
            self._say(str(error), error=True)
            return
        if opened:
            self._say('opened %s' % os.path.basename(opened))
            self.dialog.accept()

    def _import(self):
        path = self._selected_path()
        if not path:
            self._say('No version selected', error=True)
            return
        try:
            scripts.import_into_current(path)
        except scripts.ScriptError as error:
            self._say(str(error), error=True)
            return

        self._say('imported %s' % os.path.basename(path))
        self.dialog.accept()

    def _create(self, from_current):
        entity_context = self.context()
        try:
            path = scripts.create_version(entity_context, from_current=from_current)
        except scripts.ScriptError as error:
            self._say(str(error), error=True)
            return
        if not path:
            return

        self._refresh_versions()
        self._say('created %s' % os.path.basename(path))
        self.dialog.accept()
        ask_to_publish(path)

    def _settings(self):
        show_settings()
        self.reload()

    def _say(self, message, error=False):
        if not message:
            self.status.setText('')
            return
        colour = '#e06c5a' if error else '#8a8a8a'
        self.status.setText('<span style="color:%s">%s</span>' % (colour, message))

    def show(self):
        self.reload()
        self.dialog.show()
        self.dialog.raise_()


_browser = None


def show_browser():
    '''Open the browser, rebuilding it so a reloaded module is picked up.'''
    global _browser
    _browser = Browser()
    _browser.show()
    return _browser


# -- the publish prompt ------------------------------------------------------

def ask_to_publish(path):
    '''Ask for a comment and a status, then post them.'''
    entity_context, _source = stamp.read_current()
    if publish.why_not(entity_context):
        return

    QtCore, QtWidgets = _qt()

    dialog = QtWidgets.QDialog(_parent())
    dialog.setWindowTitle('Update Kitsu')
    dialog.setMinimumWidth(420)

    layout = QtWidgets.QVBoxLayout(dialog)
    layout.addWidget(QtWidgets.QLabel(entity_context.versioned()))

    comment = QtWidgets.QPlainTextEdit(publish.default_comment(path))
    comment.setMaximumHeight(90)
    layout.addWidget(comment)

    status = QtWidgets.QComboBox()
    status.addItem('Leave unchanged', None)
    for row in state.statuses:
        status.addItem(row.get('name', '?'), row['id'])
    form = QtWidgets.QFormLayout()
    form.addRow('Status', status)
    layout.addLayout(form)

    # A snapshot is a one-frame render, so it is offered rather than assumed -
    # but it is on by default, because a task with no picture is the reason
    # most shots have no thumbnail in Kitsu at all.
    source = capture.describe()
    snapshot = QtWidgets.QCheckBox(
        'Attach a snapshot of %s' % (source or 'the Viewer'))
    snapshot.setChecked(bool(source))
    snapshot.setEnabled(bool(source))
    if not source:
        snapshot.setText('Nothing to snapshot - select a node or open a Viewer')
    layout.addWidget(snapshot)

    box = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    layout.addWidget(box)

    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return

    picture, problem = (capture.snapshot() if snapshot.isChecked()
                        else (None, ''))
    try:
        note = publish.send(entity_context, path, comment=comment.toPlainText(),
                            task_status_id=status.currentData(), preview=picture)
    finally:
        if picture:
            capture.discard(picture)

    # A snapshot that was asked for and did not happen has to say so. Silently
    # posting a comment with no picture looks exactly like the feature working.
    if problem:
        import nuke
        nuke.message('The comment was posted, but no thumbnail.'
                     + chr(10) + chr(10) + problem)
    state.say(note)


def ask_to_publish_render(node, frame_count):
    """Comment and status for a rendered version, then build and upload.

    Separate from the save dialog because what is being sent is different: a
    movie built from frames on disk, not a snapshot of the Viewer, and the
    build can take a while on a long shot.
    """
    entity_context, _source = stamp.read_current()
    blocked = publish.why_not(entity_context)
    if blocked:
        import nuke
        nuke.message(blocked)
        return ''

    QtCore, QtWidgets = _qt()

    dialog = QtWidgets.QDialog(_parent())
    dialog.setWindowTitle('Publish Render to Kitsu')
    dialog.setMinimumWidth(440)

    layout = QtWidgets.QVBoxLayout(dialog)
    layout.addWidget(QtWidgets.QLabel(entity_context.versioned()))

    note = QtWidgets.QLabel(
        '%d frame(s) will be converted to H.264 for Kitsu.' % frame_count)
    note.setWordWrap(True)
    layout.addWidget(note)

    comment = QtWidgets.QPlainTextEdit('%s rendered from Nuke'
                                       % entity_context.versioned())
    comment.setMaximumHeight(90)
    layout.addWidget(comment)

    status = QtWidgets.QComboBox()
    status.addItem('Leave unchanged', None)
    for row in state.statuses:
        status.addItem(row.get('name', '?'), row['id'])
    form = QtWidgets.QFormLayout()
    form.addRow('Status', status)
    layout.addLayout(form)

    box = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    layout.addWidget(box)

    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return ''

    QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
    try:
        result = review.submit(node, comment=comment.toPlainText(),
                               task_status_id=status.currentData())
    finally:
        QtWidgets.QApplication.restoreOverrideCursor()

    if result and 'updated' not in result.lower():
        import nuke
        nuke.message(result)
    state.say(result)
    return result


# -- settings ----------------------------------------------------------------

def show_settings():
    '''Server, login and roots - the handful Nuke has nowhere else to keep.'''
    QtCore, QtWidgets = _qt()
    values = settings.load()

    dialog = QtWidgets.QDialog(_parent())
    dialog.setWindowTitle('Kitsu Settings')
    dialog.setMinimumWidth(460)

    layout = QtWidgets.QVBoxLayout(dialog)
    form = QtWidgets.QFormLayout()

    server = QtWidgets.QLineEdit(values.get('server', ''))
    server.setPlaceholderText('http://kitsu.example.com:8080')
    email = QtWidgets.QLineEdit(values.get('email', ''))
    password = QtWidgets.QLineEdit()
    password.setEchoMode(QtWidgets.QLineEdit.Password)
    password.setPlaceholderText('only needed once - then stored securely')
    work_root = QtWidgets.QLineEdit(values.get('work_root', ''))
    work_root.setPlaceholderText('blank if the Kitsu project brief sets it')
    render_root = QtWidgets.QLineEdit(values.get('render_root', ''))

    form.addRow('Kitsu Server', server)
    form.addRow('Email', email)
    form.addRow('Password', password)
    form.addRow('Work Root', work_root)
    form.addRow('Render Root', render_root)
    layout.addLayout(form)

    note = QtWidgets.QLabel(
        'The password goes to the Windows Credential Manager, never to this '
        'file - the same store Blender and the standalone tools use.')
    note.setWordWrap(True)
    layout.addWidget(note)

    result = QtWidgets.QLabel('')
    result.setWordWrap(True)
    layout.addWidget(result)

    def test():
        '''Try the settings as typed, before they are saved.

        Right here, next to the fields, because the alternative is finding out
        two dialogs later - and a wrong address reads like a wrong password
        unless something says otherwise.
        '''
        typed_server = server.text().strip()
        typed_email = email.text().strip()
        secret = password.text() or credentials.get_password(typed_email)

        if not typed_server or not typed_email:
            result.setText('<span style="color:#e06c5a">'
                           'Server and email are both needed</span>')
            return
        if not secret:
            result.setText('<span style="color:#e06c5a">'
                           'No password typed, and none stored</span>')
            return

        result.setText('Connecting...')
        QtWidgets.QApplication.processEvents()

        client = KitsuClient(typed_server,
                             verify=not values.get('allow_insecure_tls'))
        try:
            user = client.log_in(typed_email, secret)
        except (AuthError, KitsuError) as error:
            result.setText('<span style="color:#e06c5a">%s</span>'
                           % explain(error, typed_server, typed_email))
            return

        name = (user or {}).get('full_name') or typed_email
        result.setText('<span style="color:#6aa84f">Connected as %s - '
                       '%d project(s)</span>'
                       % (name, len(client.open_projects())))

    box = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    test_button = box.addButton('Test Connection',
                                QtWidgets.QDialogButtonBox.ActionRole)
    test_button.clicked.connect(test)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    layout.addWidget(box)

    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return

    settings.save({
        'server': server.text().strip(),
        'email': email.text().strip(),
        'work_root': work_root.text().strip(),
        'render_root': render_root.text().strip(),
    })

    typed = password.text()
    if typed and email.text().strip():
        credentials.set_password(email.text().strip(), typed)

    state.reset()
