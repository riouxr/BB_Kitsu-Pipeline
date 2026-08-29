'''Diagnostic: does a real PySide window work as a genuinely standalone
process, run directly - NOT through Resolve's Workspace > Scripts menu?

Fusion's own script menu runs a script inside a process that shares
Fusion's internal Qt/event-loop machinery, and a second QApplication event
loop started there is a long-documented Fusion bug ("PySide freezes
Fusion" - reported since Fusion 7). This script deliberately has nothing to
do with that: it does not import bmd, fusion, or the Resolve scripting API
at all, so it proves or disproves the PySide-in-its-own-process question on
its own, with no other variable involved.

Run this from a terminal, NOT from Resolve's Workspace > Scripts menu:

    python "I:\\Addon Developpment\\Github\\BB_Kitsu-Pipeline\\resolve\\test_standalone_window.py"

Expected result if this approach is viable: a small window appears with a
Cancel button; clicking it closes the window and the script exits cleanly.
'''
import sys

try:
    from PySide6 import QtWidgets
    print('[test] using PySide6')
except ImportError:
    try:
        from PySide2 import QtWidgets
        print('[test] using PySide2')
    except ImportError:
        print('[test] neither PySide6 nor PySide2 is importable in this Python')
        sys.exit(1)

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

win = QtWidgets.QDialog()
win.setWindowTitle('Standalone PySide Test')
win.resize(320, 120)

layout = QtWidgets.QVBoxLayout(win)
label = QtWidgets.QLabel('If you can see this and Cancel closes it, this approach works.')
label.setWordWrap(True)
cancel_btn = QtWidgets.QPushButton('Cancel')
cancel_btn.clicked.connect(win.close)
layout.addWidget(label)
layout.addWidget(cancel_btn)

win.show()
print('[test] window shown - waiting for it to close...')
app.exec()
print('[test] window closed, script exiting cleanly')
