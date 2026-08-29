'''Kitsu Publisher for DaVinci Resolve.

A thin launcher: everything that matters lives in BB_pipeline_resolve/ and
the shared BB_core/ package. No dependencies to pip install - Kitsu access
rides on BB_core.transport, which uses requests when this Python has it and
falls back to urllib when it does not.

Run this as its OWN process - a terminal, a double-clicked .bat, a desktop
shortcut - never from Resolve's Workspace > Scripts menu. Confirmed with
resolve/test_standalone_window.py: a PySide window works perfectly run
standalone and does nothing at all run through Workspace > Scripts, because
a second Qt event loop started inside Fusion's own script host conflicts
with Fusion's - a Blackmagic bug reported since Fusion 7 ("PySide freezes
Fusion"). Resolve just needs to already be running, with a project open;
this connects into it rather than being launched by it.

Install
-------
Copy this file, the BB_pipeline_resolve/ folder and the BB_core/ folder
anywhere convenient - deliberately NOT Scripts/Comp, so there is nothing
sitting in Workspace > Scripts inviting the one launch method that does not
work. Running it from a checkout of this repository works too, with nothing
to copy - the script finds BB_core two folders up on its own.

Run
---
With Resolve already open and a project loaded:

    python kitsu_resolve_publisher.py

using whichever Python has PySide6 or PySide2 installed. A double-clickable
launcher for that is one line in a .bat file (Windows) or a shell script
(macOS/Linux) - see resolve/Launch Kitsu Publisher.bat for an example.
'''
import os
import sys

# Run through Fusion's own console, print() just worked; run as a plain
# terminal process on Windows, stdout defaults to the console's codepage
# (cp1252 or similar), which cannot encode the checkmarks and arrows used
# throughout this tool's log messages - UnicodeEncodeError, not a bug in
# any one message. Reconfigure before anything else has a chance to print.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def _locate_here():
    '''Where this script lives.

    Resolve does not set ``__file__`` for a script launched from
    Workspace > Scripts - it runs the file through exec() with a bare
    globals dict - so this falls back to argv[0], and finally to Fusion's
    own map of the Scripts folder this script was found in.
    '''
    try:
        return os.path.dirname(os.path.realpath(__file__))
    except NameError:
        pass

    argv0 = sys.argv[0] if sys.argv else ''
    if argv0 and os.path.isfile(argv0):
        return os.path.dirname(os.path.realpath(argv0))

    try:
        fusion = bmd.scriptapp('Resolve').Fusion()  # noqa: F821
        mapped = fusion.MapPath('Scripts:Comp')
        if mapped and os.path.isdir(mapped):
            return os.path.realpath(mapped)
    except Exception:
        pass

    raise RuntimeError(
        'kitsu_resolve_publisher: could not determine this script\'s own '
        'folder - Resolve gave neither __file__ nor a usable argv[0], and '
        'Fusion.MapPath("Scripts:Comp") did not resolve either')


HERE = _locate_here()
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import BB_pipeline_resolve as _bb  # noqa: E402

print('[kitsu] BB_pipeline_resolve %s' % _bb.__version__)

if not _bb.core.available:
    print('[kitsu] %s' % _bb.core.error)
else:
    _bb.run()
