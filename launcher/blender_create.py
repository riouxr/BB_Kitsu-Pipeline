"""Creates a task's first Blender work file when none exists yet.

Runs ``blender_create_bg.py`` inside a background Blender, reusing the
add-on's own creation logic (see that file for why this is safe) rather
than writing an empty .blend some other way and hoping Blender's own
pipeline code treats it as a properly stamped, versioned file.
"""
import json
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().with_name("blender_create_bg.py")

CREATE_TIMEOUT = 120


def create(exe, context, path):
    """Creates *path* as this context's v001, via a background Blender.

    Raises RuntimeError on any failure - a bad exe path, Blender itself
    failing to start, a timeout, or the creation script's own reported
    failure - carrying enough of Blender's own output to diagnose it.
    """
    if not exe:
        raise RuntimeError("no blender executable configured")

    payload = json.dumps({"context": context.to_dict(), "path": str(path)})
    try:
        result = subprocess.run(
            [exe, "--background", "--python", str(_SCRIPT), "--", payload],
            capture_output=True, timeout=CREATE_TIMEOUT,
            # Not `text=True`: that decodes with the system's default
            # codepage (cp1252 here), and one of this machine's ~50 other
            # add-ons prints something outside it - a crash in the
            # subprocess module's own output-reading thread, not in
            # Blender or in anything of ours, which silently left
            # `result.stdout` as None. Decoded by hand below instead, with
            # unrepresentable bytes replaced rather than fatal.
            encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "Blender did not finish creating %s within %ds"
            % (path, CREATE_TIMEOUT))

    output = result.stdout or ""
    if "BB_LAUNCH_CREATE_OK" not in output:
        raise RuntimeError(
            "Blender could not create %s:\n%s" % (path, output[-2000:]))
