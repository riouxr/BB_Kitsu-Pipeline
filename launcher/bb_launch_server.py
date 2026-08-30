"""A tiny localhost HTTP server the Kitsu page's Launch button calls.

Exists because Chrome's handling of custom ``xyz://`` protocol handlers
turned out to be too unreliable to build on - three separate fixes (Default
Programs registration, a dedicated compiled exe, a real ``<a href>`` instead
of a JS navigation) made no difference in practice, and the failure mode
never pointed at anything actually wrong with the registration (a
byte-for-byte comparison against Discord's own, definitely-working
registration showed no difference). An ordinary HTTP request to localhost
sidesteps all of that: it is not a protocol handoff at all, so none of
Chrome's external-protocol restrictions apply.

Run this once and leave it running::

    pythonw bb_launch_server.py

The actual resolution and launch logic is not duplicated here - it lives in
bb_launch.py, imported directly, so a fix there applies to both the
`bbkitsu://`-style command-line entry point and this server without keeping
two copies in step.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bb_launch
import launcher_config
from BB_core import credentials, settings, versioning, workfiles
from BB_core.kitsu import KitsuClient, KitsuError, explain
from BB_core.workfiles import RootNotConfigured

HOST = "127.0.0.1"
PORT = 53212

# One client, reused across requests rather than logging in from scratch each
# time - launching is meant to feel instant, and a login round trip is the
# slowest part of the whole request.
_client_lock = threading.Lock()
_client = None


def _get_client():
    global _client
    with _client_lock:
        if _client is not None and _client.logged_in:
            return _client

        values = settings.load()
        server, email = values.get("server"), values.get("email")
        if not server or not email:
            raise KitsuError(
                "Kitsu server/email not set - run the Blender or Nuke add-on "
                "once to configure BB_pipeline settings")
        password = credentials.get_password(email)
        if not password:
            raise KitsuError(
                "no stored password for %s - log in once from Blender or "
                "Nuke with 'remember password' on" % email)

        client = KitsuClient(server, verify=not values.get("allow_insecure_tls"))
        client.log_in(email, password)
        _client = client
        return _client


def _resolve(task_id):
    """``(context, config, dcc, work_dir, ext)`` for a task id.

    Shared by both routes: the version list and the launch itself have to
    agree on exactly which folder and extension they mean, or the versions
    shown in the picker could disagree with what Launch actually finds.
    """
    client = _get_client()
    context = bb_launch.resolve_context(client, task_id)
    config = settings.config(client.project(context.project_id))
    dcc = bb_launch.dcc_for(context, config)
    work_dir = workfiles.work_dir(context, config)
    ext = config.dcc(dcc).get("ext", "")
    return context, config, dcc, work_dir, ext


def _handle_versions(query):
    """Every local scene version for a task, newest first.

    What the task panel's version picker is built from - Kitsu's own preview
    revision numbers are a different sequence (see launcher.js) and cannot
    stand in for this.
    """
    task_id = (query.get("task_id") or [None])[0]
    if not task_id:
        return 400, {"error": "missing task_id"}

    context, config, dcc, work_dir, ext = _resolve(task_id)
    found = versioning.existing_versions(work_dir, context.as_fields(), ext, config)
    versions = sorted((v for v, _path in found), reverse=True)
    return 200, {"versions": versions, "dcc": dcc}


def _handle_launch(query):
    task_id = (query.get("task_id") or [None])[0]
    if not task_id:
        return 400, {"error": "missing task_id"}
    version = (query.get("version") or [None])[0]
    wanted_version = int(version) if version else None

    context, config, dcc, work_dir, ext = _resolve(task_id)

    if wanted_version:
        existing = dict(versioning.existing_versions(
            work_dir, context.as_fields(), ext, config))
        if wanted_version not in existing:
            return 404, {"error":
                ("no v%03d on disk for %s / %s / %s - versions found: %s"
                 % (wanted_version, context.group, context.entity, context.task,
                    ", ".join("v%03d" % v for v in sorted(existing)) or "none"))}
        scene_path = existing[wanted_version]
    else:
        found = versioning.latest_version(
            work_dir, context.as_fields(), ext, config)
        if not found:
            return 404, {"error":
                ("no work file yet for %s / %s / %s - create one from %s first"
                 % (context.group, context.entity, context.task, dcc))}
        _, scene_path = found

    bb_launch.launch(dcc, launcher_config.get(bb_launch._EXE_SETTING[dcc]), scene_path)
    return 200, {"message": "opening %s in %s" % (scene_path, dcc)}


class Handler(BaseHTTPRequestHandler):
    # The default logs every request to stderr, which is pointless noise for
    # a background process with nowhere for stderr to go.
    def log_message(self, format, *args):
        pass

    def _cors(self):
        # Wide open on purpose: this only ever runs on 127.0.0.1, reachable
        # solely from the machine it is on, so there is no cross-origin
        # boundary here worth restricting.
        self.send_header("Access-Control-Allow-Origin", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    _ROUTES = {"/launch": _handle_launch, "/versions": _handle_versions}

    def do_GET(self):
        parsed = urlsplit(self.path)
        route = self._ROUTES.get(parsed.path)
        if route is None:
            self.send_response(404)
            self._cors()
            self.end_headers()
            return

        try:
            status, payload = route(parse_qs(parsed.query))
        except (KitsuError, RootNotConfigured) as error:
            message = explain(error) if isinstance(error, KitsuError) else str(error)
            status, payload = 502, {"error": message}
        except Exception as error:  # noqa: BLE001 - reported to the page, not swallowed
            status, payload = 500, {"error": str(error)}

        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("bb_launch_server listening on http://%s:%d" % (HOST, PORT))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
