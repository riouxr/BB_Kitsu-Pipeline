"""Kitsu REST client.

Deliberately not built on ``gazu``. Blender bundles requests but not gazu, and
gazu drags in python-socketio, pywin32 and typing_extensions - none of which
the pipeline uses - so depending on it would mean bundling wheels into every
DCC or pip-installing at launch, which is what the Resolve tool currently does
on every single run.

It does not depend on ``requests`` either, because Nuke 16 does not ship it.
HTTP goes through :mod:`BB_core.transport`, which uses requests when the
host has it and the standard library when it does not - so the core needs
nothing installed anywhere.

Routes are the ones gazu itself calls, so behaviour matches the existing tools:

    POST api/auth/login
    GET  api/data/projects/open
    GET  api/data/projects/{id}/sequences
    GET  api/data/sequences/{id}/shots
    GET  api/data/shots/{id}/tasks
    GET  api/data/projects/{id}/asset-types
    GET  api/data/projects/{id}/assets
    GET  api/data/assets/{id}/tasks
    GET  api/data/task-types
    GET  api/data/departments
    GET  api/data/task-status
    GET  api/pictures/thumbnails/preview-files/{id}.png
    POST api/actions/tasks/{id}/comment
    POST api/actions/tasks/{id}/comments/{id}/add-preview
    POST api/pictures/preview-files/{id}
    PUT  api/actions/preview-files/{id}/set-main-preview
"""

import os
import re

from .transport import TransportError, make_transport

_BARE_ADDRESS = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|localhost)(:\d+)?$")

# Connect fast, then wait a long time: a full-resolution movie preview can take
# the best part of an hour to push over a slow office link, and the earlier
# tools hit read timeouts mid-upload because of it.
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 60
UPLOAD_READ_TIMEOUT = 3600


class KitsuError(Exception):
    """Any failure talking to Kitsu."""


class AuthError(KitsuError):
    """Login failed, or the session is no longer valid."""


def statuses_for(project, statuses):
    """The statuses one project actually uses, out of the studio's list.

    Kitsu keeps task statuses studio-wide and each project picks the ones it
    uses, so the raw list is every status anybody has ever needed on any
    show. Offering all twenty when this show has four is how a shot ends up
    marked with another production's workflow.

    Falls back to the whole list when a project names none, which is what
    Kitsu itself does.
    """
    wanted = (project or {}).get("task_statuses")
    if not wanted:
        return list(statuses or [])

    allowed = set(wanted)
    picked = [row for row in (statuses or []) if row.get("id") in allowed]
    return picked or list(statuses or [])


def explain(error, server="", email=""):
    """A short, human answer to why a connection failed.

    The exception text carries the whole transport stack - urllib3 pool
    objects, retry counts, socket errors - which is useful in a log and
    useless in a dialog. Worse, it reads like a rejected password even when
    the address was simply wrong, which is exactly the wrong thing to tell
    somebody who is about to retype a correct password.
    """
    if isinstance(error, AuthError):
        detail = str(error)
        if "two-factor" in detail:
            return detail
        # Kitsu answers the same way for an unknown email as for a wrong
        # password, so naming only the password sends people to re-type
        # something that was right all along. The email is echoed because a
        # typo in it is invisible until you see it written back.
        return ("Kitsu rejected the login for %s - check the email and the "
                "password" % (email or "that account"))

    text = str(error)
    if "cannot reach" in text.lower():
        return ("Cannot reach %s - check the address, and that Kitsu is "
                "running and on the network" % (server or "the server"))
    return text


class KitsuClient:
    def __init__(self, host, session=None, verify=True):
        """``verify=False`` skips TLS certificate checking.

        Needed only to reach a server by its LAN address: a Kitsu box behind a
        certificate issued for its public hostname redirects http to https and
        then fails verification when it is addressed as a bare IP. Reaching the
        same server by hostname verifies normally and is always the better
        answer, so this stays off by default and the host application has to
        ask for it.
        """
        self.host = (host or "").strip().rstrip("/")
        if self.host and not self.host.startswith(("http://", "https://")):
            # A bare IP or localhost gets http, everything else https. A
            # certificate is issued for a hostname, so assuming https for an
            # IP produces a verification failure that reads like the server is
            # down; a Kitsu reachable at an IP is in practice plain http.
            self.host = ("http://" if _BARE_ADDRESS.match(self.host)
                         else "https://") + self.host
        self.api = self.host + "/api"

        self.transport = session or make_transport(verify)
        if not verify:
            # Otherwise every single call prints an InsecureRequestWarning,
            # which in Blender's console buries everything else.
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass
        self.access_token = ""
        self.refresh_token = ""
        self.user = None

    # -- plumbing -------------------------------------------------------------

    def _headers(self, token=None):
        headers = {"User-Agent": "BB Kitsu Pipeline"}
        token = token or self.access_token
        if token:
            headers["Authorization"] = "Bearer " + token
        return headers

    def _url(self, path):
        return "%s/%s" % (self.api, path.lstrip("/"))

    def _check(self, response, path):
        if response.status_code in (401, 403):
            raise AuthError("not authorised for %s (HTTP %s)"
                            % (path, response.status_code))
        if response.status_code >= 400:
            try:
                body = response.json()
                detail = body.get("message") or body.get("error") or ""
            except Exception:
                detail = (response.text or "")[:200]
            raise KitsuError("%s failed: HTTP %s %s"
                             % (path, response.status_code, detail))

    def _request(self, method, path, retry=True, **kwargs):
        kwargs.setdefault("timeout", (CONNECT_TIMEOUT, READ_TIMEOUT))
        kwargs.setdefault("headers", self._headers())

        try:
            response = self.transport.request(method, self._url(path), **kwargs)
        except TransportError as error:
            raise KitsuError("cannot reach %s: %s" % (self.host, error))

        # One transparent refresh, then give up - looping here would hammer the
        # server with a dead token.
        if response.status_code == 401 and retry and self.refresh_token:
            if self.refresh():
                kwargs["headers"] = self._headers()
                return self._request(method, path, retry=False, **kwargs)

        self._check(response, path)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def _fetch(self, path, params=None):
        """A ``data/`` route, unwrapping the paginated envelope if present.

        The envelope test has to be narrow. Kitsu entities carry their own
        ``data`` attribute for custom fields, so unwrapping on that key alone
        turned a single-task fetch into whatever was in the task's custom
        data - usually None. A paginated response is a dict with a *list*
        under ``data`` and no ``id`` of its own.
        """
        result = self._request("GET", "data/" + path.lstrip("/"), params=params)

        if (isinstance(result, dict)
                and "id" not in result
                and isinstance(result.get("data"), list)):
            return result["data"]

        if isinstance(result, dict):
            return result
        return result or []

    # -- session --------------------------------------------------------------

    def log_in(self, email, password):
        """Authenticate and remember the tokens. Returns the user dict."""
        if not self.host:
            raise KitsuError("no Kitsu server configured")

        try:
            response = self.transport.request(
                "POST", self._url("auth/login"),
                json={"email": email, "password": password},
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                headers={"User-Agent": "BB Kitsu Pipeline"},
            )
        except TransportError as error:
            raise KitsuError("cannot reach %s: %s" % (self.host, error))

        if response.status_code in (400, 401, 403):
            raise AuthError("Kitsu rejected the login for %s" % email)
        self._check(response, "auth/login")

        payload = response.json() or {}
        if not payload.get("access_token"):
            # Kitsu answers 200 with login=False when it wants a second factor.
            if payload.get("totp") or payload.get("email_otp") or payload.get("fido"):
                raise AuthError("this account needs two-factor auth, "
                                "which is not supported yet")
            raise AuthError("Kitsu returned no access token")

        self.access_token = payload["access_token"]
        self.refresh_token = payload.get("refresh_token", "")
        self.user = payload.get("user")
        return self.user

    def refresh(self):
        """Swap the refresh token for a new access token. True on success."""
        if not self.refresh_token:
            return False
        try:
            response = self.transport.request(
                "GET", self._url("auth/refresh-token"),
                headers=self._headers(self.refresh_token),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
        except TransportError:
            return False
        if response.status_code >= 400:
            return False

        token = (response.json() or {}).get("access_token")
        if not token:
            return False
        self.access_token = token
        # Kitsu invalidates a refresh token once it is spent.
        self.refresh_token = ""
        return True

    @property
    def logged_in(self):
        return bool(self.access_token)

    def log_out(self):
        self.access_token = ""
        self.refresh_token = ""
        self.user = None

    # -- reads ----------------------------------------------------------------

    @staticmethod
    def _by_name(items):
        return sorted(items or [], key=lambda item: (item.get("name") or "").lower())

    def open_projects(self):
        return self._by_name(self._fetch("projects/open"))

    def sequences(self, project_id):
        return self._by_name(self._fetch("projects/%s/sequences" % project_id))

    def shots(self, sequence_id):
        return self._by_name(self._fetch("sequences/%s/shots" % sequence_id))

    def shots_for_project(self, project_id):
        """Every shot in one project, in one call.

        The browser prefetches these so changing sequence filters a local list
        instead of firing a request from a UI callback.
        """
        return self._by_name(self._fetch("projects/%s/shots" % project_id))

    def task_types(self):
        return self._by_name(self._fetch("task-types"))

    def departments(self):
        """Kitsu's departments, which each task type belongs to.

        The per-DCC task filter keys on these: Blender offers the 3D
        departments, Nuke the compositing ones.
        """
        return self._by_name(self._fetch("departments"))

    def asset_types(self, project_id):
        return self._by_name(self._fetch("projects/%s/asset-types" % project_id))

    def assets_for_project(self, project_id):
        """Every asset in one project; the browser filters by type locally."""
        return self._by_name(self._fetch("projects/%s/assets" % project_id))

    def tasks_for_asset(self, asset_id):
        return self._fetch("assets/%s/tasks" % asset_id)

    def thumbnail(self, preview_file_id):
        """The PNG bytes of a preview file's thumbnail, or None.

        Addressed through the preview file rather than the entity. Zou also
        publishes `pictures/thumbnails/shots/<id>.png`, and the older Resolve
        tool uses it, but that route 404s on the studio server - so the
        entity's `preview_file_id` is what actually resolves.
        """
        if not preview_file_id:
            return None
        try:
            response = self.transport.request(
                "GET",
                self._url("pictures/thumbnails/preview-files/%s.png" % preview_file_id),
                headers=self._headers(),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
        except TransportError:
            return None

        if response.status_code != 200 or not response.content:
            return None
        return response.content

    def thumbnail_for(self, entity):
        """The thumbnail of a shot or asset dict, or None if it has none."""
        return self.thumbnail((entity or {}).get("preview_file_id"))

    def task_statuses(self):
        return self._by_name(self._fetch("task-status"))

    def tasks_for_shot(self, shot_id):
        return self._fetch("shots/%s/tasks" % shot_id)

    def task(self, task_id):
        return self._fetch("tasks/%s" % task_id)

    def shot(self, shot_id):
        """One shot, read fresh.

        Opening a scene re-asks for this rather than trusting the browser's
        cache, because catching a frame range that moved since the file was
        saved is the entire reason for looking.
        """
        return self._fetch("shots/%s" % shot_id)

    def asset(self, asset_id):
        return self._fetch("assets/%s" % asset_id)

    def project(self, project_id):
        return self._fetch("projects/%s" % project_id)

    # -- publish --------------------------------------------------------------

    def publish_preview(self, task_id, file_path, comment="", task_status_id=None,
                        set_main=True, normalize=False, log=None):
        """Comment, upload a preview and set it as the task thumbnail.

        The upload posts the file with ``requests`` rather than going through a
        helper, for two reasons found the hard way in the existing tools: large
        movie previews need the long read timeout, and an nginx 504 on a big
        file usually means Kitsu received it and the reverse proxy gave up
        waiting for the response. Treating that as fatal was losing successful
        publishes.

        ``normalize`` is off by default, which is the opposite of Kitsu's own
        default and deliberate. Normalising conforms the upload to the project
        resolution and *upscales* anything smaller - a 960x540 test render
        came back stored as 3840x2160 - and re-encodes what was already H.264.
        With it off, Zou keeps the exact bytes it was handed. The cost is that
        it no longer builds a separate low-resolution proxy, so the player
        streams the full file; thumbnails are still generated either way.
        """
        log = log or (lambda message: None)

        if not task_status_id:
            task_status_id = (self.task(task_id) or {}).get("task_status_id")
        if not task_status_id:
            raise KitsuError("no task status to publish against")

        log("posting comment")
        comment_obj = self._request(
            "POST", "actions/tasks/%s/comment" % task_id,
            json={"task_status_id": task_status_id, "comment": comment or "",
                  "checklist": [], "links": []},
        )
        if not comment_obj or not comment_obj.get("id"):
            raise KitsuError("Kitsu did not return a comment")

        log("creating preview record")
        preview = self._request(
            "POST",
            "actions/tasks/%s/comments/%s/add-preview" % (task_id, comment_obj["id"]),
            json={},
        )
        if not preview or not preview.get("id"):
            raise KitsuError("Kitsu did not return a preview record")

        size_mb = os.path.getsize(file_path) // (1024 * 1024)
        log("uploading %s MB" % size_mb)

        route = "pictures/preview-files/%s" % preview["id"]
        if not normalize:
            route += "?normalize=false"

        try:
            response = self.transport.request(
                "POST", self._url(route),
                headers=self._headers(),
                file=file_path,
                timeout=(CONNECT_TIMEOUT, UPLOAD_READ_TIMEOUT),
            )
            if response.status_code not in (200, 201):
                raise KitsuError("upload failed: HTTP %s" % response.status_code)
            uploaded = response.json()
            if uploaded:
                preview = uploaded
        except (TransportError, ValueError) as error:
            log("upload response lost (%s) - Kitsu most likely has the file" % error)

        if set_main and preview.get("id"):
            try:
                self._request("PUT",
                              "actions/preview-files/%s/set-main-preview" % preview["id"],
                              json={})
                log("set as main preview")
            except KitsuError as error:
                log("could not set main preview: %s (not fatal)" % error)

        log("published")
        return {"comment": comment_obj, "preview": preview}
