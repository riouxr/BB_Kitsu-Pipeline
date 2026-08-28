'''The Kitsu browser, as a Nuke panel.

PySide6, because that is what Nuke 16 ships. Qt is imported inside the
functions that need it so the rest of this package can be exercised by a plain
interpreter with no Qt at all - which is how it gets tested on a machine with
no Nuke licence.

Shots only and compositing tasks only, which is what lets the whole
navigation collapse into one tree: a Nuke artist never has a reason to see a
lighting or FX task, so the department column Prism spends space on has
nothing left to say.
'''
import os

from BB_core import credentials, settings, workfiles
from BB_core.kitsu import AuthError, KitsuClient, KitsuError, explain

from . import (capture, fetch, publish, review, scripts, session, stamp,
               thumbnails)

state = session.state

VERSION_ICON_WIDTH = 96
TITLE = 'Kitsu Browser'


def _version():
    from . import __version__
    return __version__


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
        # Versioned, because Nuke caches imported modules and a session
        # that was never restarted runs old code while looking identical.
        self.dialog.setWindowTitle('%s  %s' % (TITLE, _version()))
        self.dialog.resize(880, 540)

        layout = QtWidgets.QVBoxLayout(self.dialog)

        self.status = QtWidgets.QLabel('')
        self.status.setWordWrap(True)

        # One header line. The project is the only thing still a dropdown,
        # because it is the one choice that is not part of the tree.
        header = QtWidgets.QHBoxLayout()
        header.addWidget(QtWidgets.QLabel('Project'))
        self.project = QtWidgets.QComboBox()
        header.addWidget(self.project, 1)
        layout.addLayout(header)

        panes = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        # -- left: sequence / shot / task, as one tree ------------------------
        left = QtWidgets.QWidget()
        left_column = QtWidgets.QVBoxLayout(left)
        left_column.setContentsMargins(0, 0, 0, 0)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        left_column.addWidget(self.tree, 1)

        # Range, rate and size as one line. Prism gives this a panel of its
        # own with an empty thumbnail slot in it; it is three numbers.
        self.facts = QtWidgets.QLabel('')
        self.facts.setStyleSheet('color:#8a8a8a')
        left_column.addWidget(self.facts)
        panes.addWidget(left)

        # -- right: the versions, each with the picture it saved --------------
        right = QtWidgets.QWidget()
        right_column = QtWidgets.QVBoxLayout(right)
        right_column.setContentsMargins(0, 0, 0, 0)

        self.versions = QtWidgets.QListWidget()
        self.versions.setIconSize(QtCore.QSize(VERSION_ICON_WIDTH,
                                               VERSION_ICON_WIDTH * 9 // 16))
        right_column.addWidget(self.versions, 1)

        buttons = QtWidgets.QHBoxLayout()
        self.open_button = QtWidgets.QPushButton('Open')
        self.new_button = QtWidgets.QPushButton('New Version')
        self.new_from_button = QtWidgets.QPushButton('New from Current')
        for widget in (self.open_button, self.new_button, self.new_from_button):
            buttons.addWidget(widget)
        right_column.addLayout(buttons)

        # Import is its own row: the three above replace what is open, this
        # one adds to it, and a mis-click between those two is expensive.
        self.import_button = QtWidgets.QPushButton('Import into Current Script')
        right_column.addWidget(self.import_button)
        panes.addWidget(right)

        panes.setStretchFactor(0, 38)
        panes.setStretchFactor(1, 62)
        panes.setSizes([330, 550])
        layout.addWidget(panes, 1)

        layout.addWidget(self.status)

        footer = QtWidgets.QHBoxLayout()
        self.settings_button = QtWidgets.QPushButton('Settings...')
        self.refresh_button = QtWidgets.QPushButton('Refresh')
        footer.addWidget(self.settings_button)
        footer.addStretch(1)
        footer.addWidget(self.refresh_button)
        layout.addLayout(footer)

        self.project.currentIndexChanged.connect(self._on_project)
        self.tree.currentItemChanged.connect(self._on_tree)

        self.open_button.clicked.connect(self._open)
        self.new_button.clicked.connect(lambda: self._create(False))
        self.new_from_button.clicked.connect(lambda: self._create(True))
        self.import_button.clicked.connect(self._import)
        self.settings_button.clicked.connect(self._settings)
        self.refresh_button.clicked.connect(self.reload)

        self._loading = False
        self._sequence_id = ''
        self._shot_id = ''
        self._task_id = ''
        self._authoring = True
        self._renders = []

    # -- filling in -----------------------------------------------------------

    def reload(self):
        """Reconnect if needed and refill everything from Kitsu."""
        if not state.connected:
            problem = fetch.connect()
            if problem:
                self._say(problem, error=True)
                return

        self._fill(self.project, state.projects, settings.get('last_project'))

    def _fill(self, combo, rows, remembered=None):
        """Refill a combo, keeping the id on each item and restoring a choice."""
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

    # -- the tree -------------------------------------------------------------

    def _on_project(self, _index=0):
        if self._loading:
            return
        project_id = self._id(self.project)
        if not project_id:
            return
        settings.set('last_project', project_id)
        fetch.project_selected(project_id)
        self._say(state.message, state.is_error)
        self._build_tree()

    def _build_tree(self):
        """Sequences and their shots. Tasks arrive when a shot is chosen.

        Shots are already in hand - the project fetch brings the whole list -
        so they cost nothing here. Tasks are per-entity in Kitsu, so a tree
        that showed them everywhere would be one request per shot.
        """
        QtCore, QtWidgets = _qt()

        self._loading = True
        restore = None
        try:
            self.tree.clear()
            wanted_shot = settings.get('last_shot')

            for sequence in state.sequences:
                node = QtWidgets.QTreeWidgetItem(self.tree,
                                                 [sequence.get('name') or '?'])
                node.setData(0, QtCore.Qt.UserRole, ('group', sequence['id']))

                for shot in state.shots_in(sequence['id']):
                    leaf = QtWidgets.QTreeWidgetItem(node,
                                                     [shot.get('name') or '?'])
                    leaf.setData(0, QtCore.Qt.UserRole, ('entity', shot['id']))
                    if shot['id'] == wanted_shot:
                        restore = leaf
        finally:
            self._loading = False

        if restore is not None:
            restore.parent().setExpanded(True)
            self.tree.setCurrentItem(restore)
        elif self.tree.topLevelItemCount():
            self.tree.topLevelItem(0).setExpanded(True)

    def _on_tree(self, item, _previous=None):
        if self._loading or item is None:
            return

        QtCore, _QtWidgets = _qt()
        payload = item.data(0, QtCore.Qt.UserRole)
        if not payload:
            return
        kind, identifier = payload

        if kind == 'group':
            self._sequence_id = identifier
            settings.set('last_sequence', identifier)
            item.setExpanded(not item.isExpanded())
            return

        if kind == 'entity':
            parent = item.parent()
            if parent is not None:
                self._sequence_id = parent.data(0, QtCore.Qt.UserRole)[1]
                settings.set('last_sequence', self._sequence_id)
            self._shot_id = identifier
            settings.set('last_shot', identifier)
            self._load_tasks(item, identifier)
            return

        parent = item.parent()
        if parent is not None:
            above = parent.data(0, QtCore.Qt.UserRole)
            if above and above[0] == 'entity':
                self._shot_id = above[1]
        self._task_id = identifier
        settings.set('last_task', identifier)
        task = next((t for t in state.tasks if t['id'] == identifier), None)
        self._authoring = state.authors(task) if task else True
        self._refresh_versions()

    def _load_tasks(self, node, shot_id):
        """Hang this shot's compositing tasks under it and pick one."""
        QtCore, QtWidgets = _qt()

        fetch.shot_selected(shot_id)
        self._say(state.message, state.is_error)

        # Guarded because it is a caption. It runs before the tasks are
        # built, so anything it throws takes the navigation with it - which
        # is how a shot with no frame range came to look like a shot with no
        # tasks.
        try:
            self._show_facts(shot_id)
        except Exception as error:
            self.facts.setText('')
            print('BB Kitsu Pipeline: could not describe the shot (%s)' % error)

        self._loading = True
        chosen = None
        try:
            node.takeChildren()
            wanted = settings.get('last_task')
            for task in state.browsable_tasks():
                label = state.task_type_name(task.get('task_type_id', '')) or '?'
                if not state.authors(task):
                    # Said plainly, because the buttons change underneath it.
                    label = '%s  (renders)' % label
                leaf = QtWidgets.QTreeWidgetItem(node, [label])
                leaf.setData(0, QtCore.Qt.UserRole, ('task', task['id']))
                if chosen is None or task['id'] == wanted:
                    chosen = leaf
        finally:
            self._loading = False

        node.setExpanded(True)

        if chosen is None:
            self._task_id = ''
            self.versions.clear()
            self._enable_actions(False)
            return

        # One compositing task is the normal case, so selecting it rather than
        # making the artist click it again is the whole point of filtering by
        # department in the first place.
        self.tree.setCurrentItem(chosen)

    def _show_facts(self, shot_id):
        """Range, rate and size for the selected shot, as one line."""
        from BB_core import frames

        shot = state.shot(shot_id) or {}
        project = state.project(self._id(self.project)) or {}

        facts = []
        # None, not a pair, when a shot carries no frame data - which plenty
        # do. Unpacking it raised, and the raise happened before the task
        # rows were built, so the shot simply appeared to have no tasks.
        span = frames.frame_range(shot)
        if span:
            facts.append('%d-%d' % span)

        rate = frames.fps(project, shot)
        if rate:
            facts.append(frames.describe(rate))

        size = frames.resolution(project, shot)
        if size:
            facts.append('%dx%d' % size)

        self.facts.setText('  ·  '.join(facts))

    def _entity_pixmap(self, shot_id):
        """The shot's own thumbnail from Kitsu, or None.

        Kitsu cannot say what a particular work version looked like - its
        preview files are numbered by a revision counter that counts
        publishes and review comments, so none of them maps to v007 - but it
        can say what the shot is, which is what makes a row recognisable.
        """
        if not state.connected:
            return None
        try:
            return thumbnails.pixmap(state.client, state.shot(shot_id),
                                     width=VERSION_ICON_WIDTH)
        except Exception:
            return None

    def _enable_actions(self, enabled):
        self.open_button.setEnabled(enabled)
        self.import_button.setEnabled(enabled)

    def _refresh_versions(self):
        self.versions.clear()
        self._renders = []

        entity_context = self.context()
        if entity_context is None:
            self._enable_actions(False)
            return

        if self._authoring:
            self._list_scripts(entity_context)
        else:
            self._list_renders(entity_context)

        self._show_buttons()

    def _show_buttons(self):
        """Only offer what the selected task can actually do.

        A lighting task has no script to open and none to create - Nuke is
        not the application that makes it. Leaving those three buttons
        sitting there enabled would be offering to write a Nuke script into
        somebody else's task folder.
        """
        for button in (self.open_button, self.new_button, self.new_from_button):
            button.setVisible(self._authoring)

    def _list_scripts(self, entity_context):
        _QtCore, QtWidgets = _qt()

        found = fetch.list_versions(entity_context)
        config = session.config_for(entity_context)
        fallback = self._entity_pixmap(self._shot_id)

        for version, path in reversed(found):
            item = QtWidgets.QListWidgetItem(
                'v%03d    %s' % (version, os.path.basename(str(path))))
            self._decorate(item, entity_context, version, config, fallback)
            self.versions.addItem(item)

        if found:
            self.versions.setCurrentRow(0)
            self.new_button.setText('New v%03d' % (found[-1][0] + 1))
        else:
            self.new_button.setText('Create v001')
            self._say('no script yet for this task')

        self._enable_actions(bool(found))

    def _list_renders(self, entity_context):
        """Rendered sequences on disk for a task Nuke does not author.

        Read off the disk rather than out of Kitsu on purpose. Kitsu holds
        the review movie, re-encoded to H.264 and often at review
        resolution; what a comp needs is the EXR sequence that movie was
        made from, which only the render root has.
        """
        _QtCore, QtWidgets = _qt()

        config = session.config_for(entity_context)
        fallback = self._entity_pixmap(self._shot_id)

        rows = []
        for stream in sorted(getattr(config, 'streams', {}) or {'main': {}}):
            try:
                for version, pattern, first, last in workfiles.render_versions(
                        entity_context, stream, config):
                    rows.append((version, stream, pattern, first, last))
            except Exception:
                continue

        for version, stream, pattern, first, last in sorted(rows, reverse=True):
            label = 'v%03d    %s    %d-%d' % (version, stream, first, last)
            if len(rows) and stream == 'main':
                label = 'v%03d    %d-%d' % (version, first, last)
            item = QtWidgets.QListWidgetItem(label)
            self._decorate(item, entity_context, version, config, fallback)
            self.versions.addItem(item)
            self._renders.append((pattern, first, last))

        if self._renders:
            self.versions.setCurrentRow(0)
        else:
            self._say('nothing rendered yet for this task')

        self._enable_actions(bool(self._renders))

    def _decorate(self, item, entity_context, version, config, fallback):
        picture = None
        try:
            picture = thumbnails.from_file(
                workfiles.thumb_file(entity_context, 'nuke', version, config),
                width=VERSION_ICON_WIDTH)
        except Exception:
            picture = None
        if picture is None:
            picture = fallback
        if picture is not None:
            from PySide6 import QtGui
            item.setIcon(QtGui.QIcon(picture))

    def context(self):
        # From the tree's own ids. The sequence, shot and task combo boxes
        # these used to read were removed with the middle column, and reading
        # a widget that no longer exists raised before a single version row
        # could be built - an empty list rather than a visible error.
        return fetch.current_context(self._id(self.project), self._sequence_id,
                                     self._shot_id, self._task_id)

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

        # Always asked, never a setting. Which answer is right changes from
        # one open to the next - a comp being finished with is replaced, one
        # still wanted for copying nodes out of is not - so a stored choice
        # is wrong half the time, and a stored choice written by an older
        # build silently stopped this being asked at all.
        where = 'here'
        if scripts.current_path():
            where = self._ask_where(path)
            if not where:
                return

        if where == 'new':
            try:
                scripts.open_elsewhere(path)
            except scripts.ScriptError as error:
                self._say(str(error), error=True)
                return
            self._say('opening %s in a second Nuke' % os.path.basename(path))
            self.dialog.accept()
            return

        if not self._settle_unsaved(path):
            return

        try:
            opened = scripts.open_version(path, self.context())
        except scripts.ScriptError as error:
            self._say(str(error), error=True)
            return
        if opened:
            self._say('opened %s' % os.path.basename(opened))
            self.dialog.accept()

    def _ask_where(self, path):
        """'here', 'new' or '' - only asked when the setting says to."""
        _QtCore, QtWidgets = _qt()

        open_script = os.path.basename(scripts.current_path())

        box = QtWidgets.QMessageBox(self.dialog)
        box.setWindowTitle(TITLE)
        box.setText('Open %s' % os.path.basename(path))
        box.setInformativeText(
            'Replace %s, or open alongside it?  A second Nuke keeps '
            'this one on screen, which is what you want for copying '
            'nodes across.' % (open_script or 'the open script'))
        here = box.addButton('Replace This One',
                             QtWidgets.QMessageBox.AcceptRole)
        other = box.addButton('Open Alongside',
                              QtWidgets.QMessageBox.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(here)
        box.exec()

        if box.clickedButton() is here:
            return 'here'
        if box.clickedButton() is other:
            return 'new'
        return ''

    def _settle_unsaved(self, path):
        """True when it is alright to replace what is open.

        Only asked when the open script has somewhere to be saved and has
        really changed. The pipeline stamps the root and repoints Writes, so
        a script can be 'modified' without the artist having touched it -
        which is why this compares against the file on disk rather than
        trusting nuke.modified() alone.
        """
        _QtCore, QtWidgets = _qt()

        if not scripts.is_modified() or not scripts.current_path():
            return True

        box = QtWidgets.QMessageBox(self.dialog)
        box.setWindowTitle(TITLE)
        box.setText('%s has unsaved changes.'
                    % os.path.basename(scripts.current_path()))
        box.setInformativeText('Save it before opening %s?'
                               % os.path.basename(path))
        save = box.addButton('Save', QtWidgets.QMessageBox.AcceptRole)
        discard = box.addButton("Don't Save", QtWidgets.QMessageBox.DestructiveRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.exec()

        if box.clickedButton() is save:
            try:
                scripts.save_open_script()
            except scripts.ScriptError as error:
                self._say(str(error), error=True)
                return False
            return True
        return box.clickedButton() is discard

    def _import(self):
        if not self._authoring:
            self._read_selected_render()
            return

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

    def _read_selected_render(self):
        """Bring a rendered sequence in as a Read node.

        The sequence off the render root, not the movie off Kitsu. Kitsu's
        copy is re-encoded H.264 for review; comping against it would throw
        away the float data and the resolution the render was made at.
        """
        row = self.versions.currentRow()
        if row < 0 or row >= len(self._renders):
            self._say('No render selected', error=True)
            return

        pattern, first, last = self._renders[row]
        try:
            read = scripts.read_sequence(pattern, first, last)
        except scripts.ScriptError as error:
            self._say(str(error), error=True)
            return

        self._say('read %s' % os.path.basename(pattern))
        if read is not None:
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
