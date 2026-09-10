'''The Kitsu browser, as a Maya dialog.

PySide6, because that is what Maya 2025 and later ship. Qt is imported inside
the functions that need it so the rest of this package can be exercised by a
plain interpreter with no Qt at all.

One tree on the left, the versions and their pictures on the right, and no
middle column: the department a task belongs to is not worth a pane of its
own. Unlike Nuke, both branches are here - Maya works on shots and assets
alike, and an artist who rigs in the morning and lights in the afternoon
should not need two different tools.
'''
import os

from BB_core import credentials, settings, workfiles
from BB_core.kitsu import AuthError, KitsuClient, KitsuError, explain

from . import (capture, fetch, publish, review, scenes, session, stamp,
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
    '''Maya's main window, so the dialog behaves like part of the app.'''
    try:
        from PySide6 import QtWidgets
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if widget.objectName() == 'MayaWindow':
                return widget
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if widget.inherits('QMainWindow') and widget.parent() is None:
                return widget
    except Exception:
        pass
    return None


def _warn(text):
    '''Say something that must not be missed, without importing maya up top.'''
    try:
        import maya.cmds as cmds
        cmds.confirmDialog(title=TITLE, message=text, button=['OK'])
    except Exception:
        print('[Kitsu] %s' % text)


class Browser(object):
    '''Built as a plain object holding a dialog, not a QDialog subclass.

    Subclassing across a reload leaves Maya holding a stale class object and
    the second call fails in a way that reads like the panel is broken. This
    keeps the Qt objects behind an ordinary Python instance that can be
    thrown away and rebuilt.
    '''

    def __init__(self):
        QtCore, QtWidgets = _qt()

        self.dialog = QtWidgets.QDialog(_parent())
        # Versioned, because Maya caches imported modules and a session that
        # was never restarted runs old code while looking identical.
        self.dialog.setWindowTitle('%s  %s' % (TITLE, _version()))
        self.dialog.resize(900, 560)

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

        # -- left: shots and assets, both as one tree -------------------------
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

        # Their own row: the three above replace what is open, these two add
        # to it, and a mis-click between those two is expensive.
        adds = QtWidgets.QHBoxLayout()
        self.reference_button = QtWidgets.QPushButton('Reference into Current')
        self.import_button = QtWidgets.QPushButton('Import into Current')
        adds.addWidget(self.reference_button)
        adds.addWidget(self.import_button)
        right_column.addLayout(adds)

        # Shown instead of the five above for a task Maya does not author.
        self.folder_button = QtWidgets.QPushButton('Open Render Folder')
        right_column.addWidget(self.folder_button)

        panes.addWidget(right)

        panes.setStretchFactor(0, 38)
        panes.setStretchFactor(1, 62)
        panes.setSizes([340, 560])
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
        self.import_button.clicked.connect(lambda: self._bring_in(False))
        self.reference_button.clicked.connect(lambda: self._bring_in(True))
        self.folder_button.clicked.connect(self._open_render_folder)
        self.settings_button.clicked.connect(self._settings)
        self.refresh_button.clicked.connect(self.reload)

        self._loading = False
        self._is_asset = False
        self._group_id = ''
        self._entity_id = ''
        self._task_id = ''
        self._authoring = True
        self._renders = []

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
        '''Shots and assets, each under a branch of its own.

        Both are already in hand - the project fetch brings the whole of each
        list - so they cost nothing here. Tasks are per-entity in Kitsu, so a
        tree that showed them everywhere would be one request per entity.

        Two roots rather than a tab: a rigger and a lighter work on opposite
        branches all day, and making that a mode to switch is how you end up
        creating a shot file under an asset.
        '''
        QtCore, QtWidgets = _qt()

        self._loading = True
        restore = None
        try:
            self.tree.clear()
            wanted = settings.get('last_entity')

            shots = QtWidgets.QTreeWidgetItem(self.tree, ['Shots'])
            shots.setData(0, QtCore.Qt.UserRole, ('branch', 'shot'))
            for sequence in state.sequences:
                node = QtWidgets.QTreeWidgetItem(shots,
                                                 [sequence.get('name') or '?'])
                node.setData(0, QtCore.Qt.UserRole, ('group', sequence['id'], False))
                for shot in state.shots_in(sequence['id']):
                    leaf = QtWidgets.QTreeWidgetItem(node, [shot.get('name') or '?'])
                    leaf.setData(0, QtCore.Qt.UserRole,
                                 ('entity', shot['id'], False))
                    if shot['id'] == wanted:
                        restore = leaf

            assets = QtWidgets.QTreeWidgetItem(self.tree, ['Assets'])
            assets.setData(0, QtCore.Qt.UserRole, ('branch', 'asset'))
            for asset_type in state.asset_types:
                node = QtWidgets.QTreeWidgetItem(assets,
                                                 [asset_type.get('name') or '?'])
                node.setData(0, QtCore.Qt.UserRole,
                             ('group', asset_type['id'], True))
                for asset in state.assets_of(asset_type['id']):
                    leaf = QtWidgets.QTreeWidgetItem(node, [asset.get('name') or '?'])
                    leaf.setData(0, QtCore.Qt.UserRole,
                                 ('entity', asset['id'], True))
                    if asset['id'] == wanted:
                        restore = leaf
        finally:
            self._loading = False

        if restore is not None:
            node = restore.parent()
            while node is not None:
                node.setExpanded(True)
                node = node.parent()
            self.tree.setCurrentItem(restore)
        else:
            for index in range(self.tree.topLevelItemCount()):
                self.tree.topLevelItem(index).setExpanded(True)

    def _on_tree(self, item, _previous=None):
        if self._loading or item is None:
            return

        QtCore, _QtWidgets = _qt()
        payload = item.data(0, QtCore.Qt.UserRole)
        if not payload:
            return

        kind = payload[0]

        if kind in ('branch', 'group'):
            item.setExpanded(not item.isExpanded())
            if kind == 'group':
                self._group_id = payload[1]
                self._is_asset = payload[2]
                settings.set('last_group', payload[1])
            return

        if kind == 'entity':
            self._is_asset = payload[2]
            parent = item.parent()
            if parent is not None:
                above = parent.data(0, QtCore.Qt.UserRole)
                if above and above[0] == 'group':
                    self._group_id = above[1]
                    settings.set('last_group', above[1])
            self._entity_id = payload[1]
            settings.set('last_entity', payload[1])
            self._load_tasks(item, payload[1])
            return

        # A task. Its entity is the row above it.
        parent = item.parent()
        if parent is not None:
            above = parent.data(0, QtCore.Qt.UserRole)
            if above and above[0] == 'entity':
                self._entity_id = above[1]
                self._is_asset = above[2]
        self._task_id = payload[1]
        settings.set('last_task', payload[1])
        task = state.task(payload[1])
        self._authoring = state.authors(task) if task else True
        self._refresh_versions()

    def _load_tasks(self, node, entity_id):
        '''Hang this entity's tasks under it and pick one.'''
        QtCore, QtWidgets = _qt()

        fetch.entity_selected(entity_id, self._is_asset)
        self._say(state.message, state.is_error)

        # Guarded because it is a caption. It runs before the tasks are built,
        # so anything it throws takes the navigation with it - which is how a
        # shot with no frame range came to look like a shot with no tasks.
        try:
            self._show_facts(entity_id)
        except Exception as error:
            self.facts.setText('')
            print('BB Kitsu Pipeline: could not describe the entity (%s)' % error)

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

        self.tree.setCurrentItem(chosen)

    def _show_facts(self, entity_id):
        '''Range, rate and size for the selected entity, as one line.'''
        from BB_core import frames

        entity = state.entity(entity_id, self._is_asset) or {}
        project = state.project(self._id(self.project)) or {}

        facts = []
        # None, not a pair, when an entity carries no frame data - which every
        # asset does, and plenty of shots. Unpacking it raised, and the raise
        # happened before the task rows were built, so the entity simply
        # appeared to have no tasks.
        if not self._is_asset:
            span = frames.frame_range(entity)
            if span:
                facts.append('%d-%d' % span)

        rate = frames.fps(project, entity)
        if rate and not self._is_asset:
            facts.append(frames.describe(rate))

        size = frames.resolution(project, entity)
        if size:
            facts.append('%dx%d' % size)

        self.facts.setText('  ·  '.join(facts))

    def _entity_pixmap(self):
        '''The entity's own thumbnail from Kitsu, or None.

        Kitsu cannot say what a particular work version looked like - its
        preview files are numbered by a revision counter that counts
        publishes and review comments, so none of them maps to v007 - but it
        can say what the shot or asset is, which is what makes a row
        recognisable.
        '''
        if not state.connected:
            return None
        try:
            return thumbnails.pixmap(state.client,
                                     state.entity(self._entity_id, self._is_asset),
                                     width=VERSION_ICON_WIDTH)
        except Exception:
            return None

    def _enable_actions(self, enabled):
        for button in (self.open_button, self.import_button,
                       self.reference_button):
            button.setEnabled(enabled)

    def _refresh_versions(self):
        self.versions.clear()
        self._renders = []

        entity_context = self.context()
        if entity_context is None:
            self._enable_actions(False)
            return

        if self._authoring:
            self._list_scenes(entity_context)
        else:
            self._list_renders(entity_context)

        self._show_buttons()

    def _show_buttons(self):
        '''Only offer what the selected task can actually do.

        A compositing task has no Maya scene to open and none to create -
        Maya is not the application that makes it. Leaving those buttons
        enabled would be offering to write a Maya scene into somebody else's
        task folder.
        '''
        for button in (self.open_button, self.new_button, self.new_from_button,
                       self.import_button, self.reference_button):
            button.setVisible(self._authoring)
        self.folder_button.setVisible(not self._authoring)

    def _list_scenes(self, entity_context):
        _QtCore, QtWidgets = _qt()

        found = fetch.list_versions(entity_context)
        config = session.config_for(entity_context)
        fallback = self._entity_pixmap()

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
            self._say('no scene yet for this task')

        self._enable_actions(bool(found))

    def _list_renders(self, entity_context):
        """What a task Maya does not author has produced.

        Read off the disk rather than out of Kitsu on purpose. Kitsu holds
        the review movie, re-encoded and often at review resolution; what is
        worth looking at from here is the render itself.
        """
        _QtCore, QtWidgets = _qt()

        config = session.config_for(entity_context)
        fallback = self._entity_pixmap()
        streams = sorted(getattr(config, 'streams', {}) or {'main': {}})

        rows = []
        for stream in streams:
            try:
                for version, pattern, first, last in workfiles.render_versions(
                        entity_context, stream, config):
                    rows.append((version, stream, pattern, first, last))
            except Exception:
                continue

        for version, stream, pattern, first, last in sorted(rows, reverse=True):
            label = 'v%03d    %s    %d-%d' % (version, stream, first, last)
            if stream == 'main':
                label = 'v%03d    %d-%d' % (version, first, last)
            item = QtWidgets.QListWidgetItem(label)
            self._decorate(item, entity_context, version, config, fallback)
            self.versions.addItem(item)
            self._renders.append((pattern, first, last))

        if self._renders:
            self.versions.setCurrentRow(0)
        else:
            self._say('nothing rendered yet for this task')

        self.folder_button.setEnabled(bool(self._renders))

    def _decorate(self, item, entity_context, version, config, fallback):
        picture = None
        try:
            picture = thumbnails.from_file(
                workfiles.thumb_file(entity_context, session.DCC, version, config),
                width=VERSION_ICON_WIDTH)
        except Exception:
            picture = None
        if picture is None:
            picture = fallback
        if picture is not None:
            from PySide6 import QtGui
            item.setIcon(QtGui.QIcon(picture))

    def context(self):
        # From the tree's own ids, never from a widget: reading a combo that
        # no longer exists raised before a single version row could be built,
        # which showed up as an empty list rather than a visible error.
        return fetch.current_context(self._id(self.project), self._group_id,
                                     self._entity_id, self._task_id,
                                     self._is_asset)

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
        # one open to the next - a scene being finished with is replaced, one
        # still wanted for copying nodes out of is not - so a stored choice
        # is wrong half the time.
        where = 'here'
        if scenes.current_path():
            where = self._ask_where(path)
            if not where:
                return

        if where == 'new':
            try:
                scenes.open_elsewhere(path)
            except scenes.SceneError as error:
                self._say(str(error), error=True)
                return
            self._say('opening %s in a second Maya' % os.path.basename(path))
            self.dialog.accept()
            return

        if not self._settle_unsaved(path):
            return

        try:
            opened = scenes.open_version(path, self.context())
        except scenes.SceneError as error:
            self._say(str(error), error=True)
            return

        from . import scenesync
        note = scenesync.on_open(stamp.read_current()[0])
        if note:
            state.say(note)

        self._say('opened %s' % os.path.basename(opened))
        self.dialog.accept()

    def _ask_where(self, path):
        """'here', 'new' or '' - only asked when something is already open."""
        _QtCore, QtWidgets = _qt()

        open_scene = os.path.basename(scenes.current_path())

        box = QtWidgets.QMessageBox(self.dialog)
        box.setWindowTitle(TITLE)
        box.setText('Open %s' % os.path.basename(path))
        box.setInformativeText(
            'Replace %s, or open alongside it?  A second Maya keeps this one '
            'on screen, which is what you want for copying nodes across.'
            % (open_scene or 'the open scene'))
        here = box.addButton('Replace This One', QtWidgets.QMessageBox.AcceptRole)
        other = box.addButton('Open Alongside', QtWidgets.QMessageBox.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(here)
        box.exec()

        if box.clickedButton() is here:
            return 'here'
        if box.clickedButton() is other:
            return 'new'
        return ''

    def _settle_unsaved(self, path):
        '''True when it is alright to replace what is open.

        Only asked when the open scene has somewhere to be saved and has
        really changed. The pipeline stamps the scene and aims the render
        output, so it can be "modified" without the artist having touched it
        - which is why each of those clears the flag again once it is done.
        '''
        _QtCore, QtWidgets = _qt()

        if not scenes.is_modified() or not scenes.current_path():
            return True

        box = QtWidgets.QMessageBox(self.dialog)
        box.setWindowTitle(TITLE)
        box.setText('%s has unsaved changes.'
                    % os.path.basename(scenes.current_path()))
        box.setInformativeText('Save it before opening %s?'
                               % os.path.basename(path))
        save = box.addButton('Save', QtWidgets.QMessageBox.AcceptRole)
        discard = box.addButton("Don't Save",
                                QtWidgets.QMessageBox.DestructiveRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.exec()

        if box.clickedButton() is save:
            try:
                scenes.save_open_scene()
            except scenes.SceneError as error:
                self._say(str(error), error=True)
                return False
            return True
        return box.clickedButton() is discard

    def _bring_in(self, as_reference):
        '''Reference or import the selected version into the open scene.

        Two buttons rather than one with a checkbox, because the difference
        is not a preference: a reference keeps tracking the file and an
        import does not, and which of those you wanted is not something to
        discover a week later.
        '''
        path = self._selected_path()
        if not path:
            self._say('No version selected', error=True)
            return

        try:
            if as_reference:
                scenes.reference_into_current(path)
            else:
                scenes.import_into_current(path)
        except scenes.SceneError as error:
            self._say(str(error), error=True)
            return

        self._say('%s %s' % ('referenced' if as_reference else 'imported',
                             os.path.basename(path)))
        self.dialog.accept()

    def _open_render_folder(self):
        '''Show the rendered frames of a task Maya does not author.

        Maya has nothing useful to do with somebody else's EXR sequence -
        there is no Read node here - so this offers the one honest action
        instead of pretending to import it.
        '''
        row = self.versions.currentRow()
        if row < 0 or row >= len(self._renders):
            self._say('No render selected', error=True)
            return

        pattern = self._renders[row][0]
        folder = os.path.dirname(str(pattern))
        if not os.path.isdir(folder):
            self._say('That folder is gone: %s' % folder, error=True)
            return

        try:
            import subprocess
            import sys as _sys
            if _sys.platform.startswith('win'):
                os.startfile(folder)                       # noqa: S606
            elif _sys.platform == 'darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])
        except Exception as error:
            self._say('Could not open %s (%s)' % (folder, error), error=True)
            return
        self._say(folder)

    def _create(self, from_current):
        entity_context = self.context()
        try:
            path, note = scenes.create_version(entity_context,
                                               from_current=from_current)
        except scenes.SceneError as error:
            self._say(str(error), error=True)
            return
        if not path:
            return

        self._refresh_versions()
        self._say('created %s%s' % (os.path.basename(path),
                                    ' - %s' % note if note else ''))
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

    _QtCore, QtWidgets = _qt()

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

    # A playblast of one frame, offered rather than assumed - but on by
    # default, because a task with no picture is the reason most entities
    # have no thumbnail in Kitsu at all.
    source = capture.describe()
    snapshot = QtWidgets.QCheckBox('Attach a snapshot of %s'
                                   % (source or 'the viewport'))
    snapshot.setChecked(bool(source))
    snapshot.setEnabled(bool(source))
    if not source:
        snapshot.setText('No viewport to snapshot')
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
        _warn('The comment was posted, but no thumbnail.' + chr(10) * 2 + problem)
    state.say(note)


def ask_to_publish_render(entity_context=None):
    """Send a frame out of this version's render to Kitsu.

    A still, not a movie. Maya renders EXR and Kitsu cannot show one, so the
    frame is picked out of whatever viewable formats the render produced -
    and if there are none, that is said rather than guessed at.
    """
    _QtCore, QtWidgets = _qt()

    if entity_context is None:
        entity_context, _source = stamp.read_current()
    blocked = publish.why_not(entity_context)
    if blocked:
        _warn(blocked)
        return ''

    try:
        config = session.config_for(entity_context)
        pattern = workfiles.render_output(entity_context, 'main', config)
    except Exception as error:
        _warn('No render path for this version (%s)' % error)
        return ''

    found = review.rendered_frames(str(pattern))
    if not found:
        _warn('Nothing rendered there yet:' + chr(10) * 2 + str(pattern))
        return ''

    frame, problem = review.pick_frame(found)
    if not frame:
        _warn(problem)
        return ''

    first, last = review.frame_span(str(pattern), found)
    text = '%s rendered %d-%d' % (entity_context.versioned(), first, last)

    note = publish.send(entity_context, str(frame), comment=text,
                        preview=str(frame))
    state.say(note)
    return note


def show_settings():
    '''Server, login and roots - the handful Maya has nowhere else to keep.'''
    _QtCore, QtWidgets = _qt()
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
        'The password goes to the operating system credential store, never '
        'to this file - the same store Blender and Nuke use.')
    note.setWordWrap(True)
    layout.addWidget(note)

    result = QtWidgets.QLabel('')
    result.setWordWrap(True)
    layout.addWidget(result)

    def test():
        '''Try the settings as typed, before they are saved.

        Right here, next to the fields, because the alternative is finding
        out two dialogs later - and a wrong address reads like a wrong
        password unless something says otherwise.
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
                           'No password typed and none stored</span>')
            return

        client = KitsuClient(typed_server,
                             verify=not values.get('allow_insecure_tls'))
        try:
            client.log_in(typed_email, secret)
            projects = client.open_projects()
        except (AuthError, KitsuError) as error:
            result.setText('<span style="color:#e06c5a">%s</span>'
                           % explain(error, typed_server, typed_email))
            return

        name = (client.user or {}).get('full_name') or typed_email
        result.setText('<span style="color:#7db37d">connected as %s - %d '
                       'project(s)</span>' % (name, len(projects or [])))

    buttons = QtWidgets.QHBoxLayout()
    test_button = QtWidgets.QPushButton('Test Connection')
    test_button.clicked.connect(test)
    buttons.addWidget(test_button)
    buttons.addStretch(1)
    box = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    buttons.addWidget(box)
    layout.addLayout(buttons)

    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return

    settings.save({
        'server': server.text().strip(),
        'email': email.text().strip(),
        'work_root': work_root.text().strip(),
        'render_root': render_root.text().strip(),
    })
    typed = password.text()
    if typed:
        credentials.set_password(email.text().strip(), typed)

    # Forced, so the next call logs in with what was just typed rather than
    # with the session that was opened using the old address.
    state.client = None
