@echo off
rem Double-click launcher for the Kitsu Publisher.
rem
rem Runs the tool as its own process, deliberately NOT through Resolve's
rem Workspace > Scripts menu - see kitsu_resolve_publisher.py for why.
rem Resolve must already be running with a project open.
rem
rem Uses whichever "python" is first on PATH. If that Python does not have
rem PySide6/PySide2 installed, edit the line below to point at the one that
rem does - e.g. the interpreter configured for Resolve's own scripting.

python "%~dp0kitsu_resolve_publisher.py"
pause
