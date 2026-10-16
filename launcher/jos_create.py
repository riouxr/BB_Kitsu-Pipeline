"""Creates a compositing task's first JOS project when none exists yet.

Runs ``jos_create_bg.py`` in ``jos-python``, JOS Compositor's headless
interpreter, which sits next to ``JOSCompositor.exe``. The same shape as
``blender_create.py``: wait for it, read its verdict, then Launch opens the
new file in JOS like any other version.
"""
import json
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().with_name("jos_create_bg.py")

CREATE_TIMEOUT = 120


def interpreter(exe):
    """``jos-python.exe`` beside the JOS executable, or ''."""
    if not exe:
        return ""
    found = Path(exe).with_name("jos-python.exe")
    return str(found) if found.is_file() else ""


def create(exe, context, path, shot=None, project=None):
    """Creates *path* as this context's v001 through jos-python.

    *exe* is JOSCompositor.exe. Raises RuntimeError with jos-python's own
    output on any failure.
    """
    runner = interpreter(exe)
    if not runner:
        raise RuntimeError(
            "no jos-python next to the JOS executable (%s) - set it with "
            "python dcc_versions.py set jos <path>" % (exe or "not found"))

    payload = json.dumps({"context": context.to_dict(), "path": str(path),
                          "shot": shot or {}, "project": project or {}})
    try:
        result = subprocess.run(
            [runner, "--no-plugins", str(_SCRIPT), payload],
            capture_output=True, timeout=CREATE_TIMEOUT,
            encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise RuntimeError("JOS did not finish creating %s within %ds"
                           % (path, CREATE_TIMEOUT))
    except OSError as error:
        raise RuntimeError("could not start %s: %s" % (runner, error))

    output = (result.stdout or "") + (result.stderr or "")
    if "BB_LAUNCH_CREATE_OK" not in output or not Path(path).is_file():
        raise RuntimeError("JOS could not create %s:\n%s"
                           % (path, output[-2000:]))
    return path
