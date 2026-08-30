"""Opens a Kitsu task's DaVinci Resolve project.

Resolve does not fit bb_launch.py's model at all: a Resolve project is not a
file on disk to Popen a path at. It lives inside Resolve's own project
database, addressed by name (``Project_Sequence_Shot_Task_vNNN``), and a new
version is a newly-imported project rather than a new file next to the old
one. The naming and versioning rules already exist in
``resolve/BB_pipeline_resolve/resolve_ops.py`` for the Resolve UI itself;
this module reuses them directly rather than re-deriving the same rules a
second time.

Also unlike Blender/Nuke, Resolve has to already be reachable through its
own scripting API before anything else is possible - starting the process
with Popen does not, by itself, mean this script can talk to it. The two
connect independently, and a freshly-launched Resolve needs real time before
its scripting service is even listening (unverified below: this module has
only ever been exercised against a Resolve that was already running -
ensure_running's launch-and-wait path is written to the same documented
polling pattern used elsewhere, but never actually watched carry a cold
start through to a connection).
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resolve.BB_pipeline_resolve import resolve_ops

CONNECT_POLL_INTERVAL = 2
CONNECT_TIMEOUT = 90


def is_running():
    """True when Resolve is up and answering its scripting API right now.

    Never trusts ``resolve_ops``'s cached connection on its own: that cache
    only ever records "did connecting succeed once," and keeps returning
    the same (by then dead) object forever after Resolve closes - this
    process outlives any one Resolve session, unlike the Resolve-hosted
    scripts ``resolve_ops`` was written for, where reconnecting was never a
    concern. A live call (``GetProjectManager``) is what actually tells a
    dead connection from a real one; on failure the cache is cleared so the
    next attempt - the very next poll, or after ``ensure_running`` relaunches
    Resolve - gets a genuine new connection instead of the same dead one.
    """
    try:
        resolve = resolve_ops.get_resolve()
        if resolve is not None and resolve.GetProjectManager() is not None:
            return True
    except Exception:
        pass
    resolve_ops._resolve_cache = None
    return False


def wait_for_connection(timeout=CONNECT_TIMEOUT):
    """Poll until Resolve's scripting API answers, or *timeout* runs out.

    The only way to know a freshly-launched Resolve is ready: there is no
    event to wait on, and ``get_resolve()`` does not cache a failed attempt
    (it only caches a real connection), so calling it again is always a
    genuine retry.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_running():
            return True
        time.sleep(CONNECT_POLL_INTERVAL)
    return False


def ensure_running(exe):
    """True once Resolve is reachable through its scripting API.

    Launches it via *exe* first if it is not already running. Returns False
    rather than raising when it cannot get there - no exe configured, or
    Resolve never comes up - so the caller can report one clear message
    instead of an exception from three layers down.
    """
    if is_running():
        return True
    if not exe:
        return False
    subprocess.Popen([exe])
    return wait_for_connection()


def resolve_versions(context):
    """Every Resolve version of this task, as ``(version, project_name)``,
    newest first - the Resolve equivalent of ``versioning.existing_versions``,
    reading Resolve's own project database instead of a folder on disk.
    """
    base = resolve_ops.build_project_base(
        context.project, context.group, context.entity, context.task)
    existing = resolve_ops.get_all_resolve_project_names()
    return sorted(resolve_ops.matching_versions(base, existing), reverse=True)


def open_project(name):
    """Switches Resolve to the project *name*. Raises RuntimeError on failure."""
    pm = resolve_ops.get_project_manager()
    if not pm.LoadProject(name):
        raise RuntimeError("Resolve could not open %r" % name)
