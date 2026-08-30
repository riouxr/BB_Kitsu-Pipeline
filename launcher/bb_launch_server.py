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
from BB_core import credentials, settings
from BB_core.kitsu import KitsuClient, KitsuError, explain
from BB_core.workfiles import RootNotConfigured

HOST = "127.0.0.1"
PORT = 53212

# Session tokens for whichever account is authenticating (see bot_email in
# launcher_config.py) - saved so a restart resumes instead of logging in
# fresh every time, which is worth avoiding on its own even though it turned
# out not to be what was booting the artist's browser tab (see bot_email's
# own comment in launcher_config.py for that story).
SESSION_FILE = Path.home() / ".BB_pipeline" / "launcher_session.json"

# One client, reused across requests rather than logging in from scratch each
# time - launching is meant to feel instant, and a login round trip is the
# slowest part of the whole request.
_client_lock = threading.Lock()
_client = None


def _load_session():
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _save_session(client, email):
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SESSION_FILE, "w", encoding="utf-8") as handle:
        json.dump({
            "server": client.host,
            "email": email,
            "access_token": client.access_token,
            "refresh_token": client.refresh_token,
        }, handle, indent=2)


def _log_in_fresh(server, email, verify):
    password = credentials.get_password(email)
    if not password:
        raise KitsuError(
            "no stored password for %s - log in once from Blender or "
            "Nuke with 'remember password' on" % email)
    client = KitsuClient(server, verify=verify)
    client.log_in(email, password)
    print("[BB] logged in fresh as %s" % email, flush=True)
    return client


def _get_client():
    global _client
    with _client_lock:
        if _client is not None and _client.logged_in:
            return _client

        values = settings.load()
        server = values.get("server")
        # A dedicated bot account, not the artist's own login: Kitsu allows
        # only one active session per person, so authenticating here as the
        # same person the browser is already logged in as boots that browser
        # tab every time this makes a request - proven by testing a resumed,
        # no-login-call session and seeing it happen anyway. Falls back to
        # the shared artist email only until a bot account is configured.
        email = launcher_config.get("bot_email") or values.get("email")
        if not server or not email:
            raise KitsuError(
                "Kitsu server/email not set - run the Blender or Nuke add-on "
                "once to configure BB_pipeline settings")
        verify = not values.get("allow_insecure_tls")

        client = KitsuClient(server, verify=verify)  # normalizes the host
        saved = _load_session()
        if (saved and saved.get("server") == client.host
                and saved.get("email") == email and saved.get("access_token")):
            client.access_token = saved["access_token"]
            client.refresh_token = saved.get("refresh_token", "")
            try:
                client.open_projects()  # cheapest authenticated call available
                print("[BB] resumed saved session, no login needed", flush=True)
                _client = client
                return _client
            except Exception:
                if client.refresh():
                    print("[BB] resumed saved session via refresh", flush=True)
                    _save_session(client, email)
                    _client = client
                    return _client

        client = _log_in_fresh(server, email, verify)
        _save_session(client, email)
        _client = client
        return _client


def _resolve(task_id):
    """``(context, config, dcc)`` for a task id.

    Shared by both routes, and by ``bb_launch.py``'s own CLI - one place
    decides what a task id means, so the version list and the launch itself
    can never disagree about it.
    """
    client = _get_client()
    context = bb_launch.resolve_context(client, task_id)
    config = settings.config(client.project(context.project_id))
    dcc = bb_launch.dcc_for(context, config)
    return context, config, dcc


def _handle_versions(query):
    """Every local version of a task's work, newest first.

    What the task panel's version picker is built from - Kitsu's own preview
    revision numbers are a different sequence (see launcher.js) and cannot
    stand in for this. A ``Path`` (Blender/Nuke) or a Resolve project name
    stringifies fine either way, so the identifier is dropped here and only
    the version numbers go back - nothing downstream of this route needs it.
    """
    task_id = (query.get("task_id") or [None])[0]
    if not task_id:
        return 400, {"error": "missing task_id"}

    context, config, dcc = _resolve(task_id)
    versions = [v for v, _identifier in bb_launch.list_versions(context, config, dcc)]
    return 200, {"versions": versions, "dcc": dcc}


def _handle_launch(query):
    task_id = (query.get("task_id") or [None])[0]
    if not task_id:
        return 400, {"error": "missing task_id"}
    version = (query.get("version") or [None])[0]
    wanted_version = int(version) if version else None

    context, config, dcc = _resolve(task_id)
    version, identifier = bb_launch.open_version(context, config, dcc, wanted_version)
    return 200, {"message": "opening %s in %s" % (identifier, dcc)}


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
        # Chrome's Private Network Access policy blocks a page served from a
        # LAN address (Kitsu, at 192.168.x.x) from reaching 127.0.0.1 unless
        # the preflight explicitly allows it - without this header the fetch
        # fails at the browser level before this server even sees a GET.
        self.send_header("Access-Control-Allow-Private-Network", "true")

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
            import traceback
            traceback.print_exc()
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
