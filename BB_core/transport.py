"""HTTP with no dependencies, and ``requests`` when it happens to be there.

The core has to run inside whatever Python a DCC ships, and they do not agree:

* Blender bundles ``requests``.
* **Nuke 16 does not.** Its Python 3.11 has ``urllib``, ``ssl``, ``certifi``
  and nothing else useful, and pip-installing into a Foundry install directory
  is not something a pipeline should be doing to an artist's machine.
* Houdini and Maya are their own stories again.

So this wraps the two, with the same small surface either way. ``requests`` is
preferred when importable because it is better tested and streams uploads
without help; the urllib path exists so the core needs nothing at all.

Only what the Kitsu client actually uses is implemented - JSON in, JSON out,
and one multipart file upload. This is not a general HTTP library and should
not grow into one.
"""

import io
import json as jsonlib
import mimetypes
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid

try:
    import requests as _requests
except ImportError:
    _requests = None

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 60


class TransportError(Exception):
    """The request never produced a response - DNS, refused, timed out."""


class Response:
    """The bits of a response the Kitsu client cares about."""

    def __init__(self, status_code, content, headers=None):
        self.status_code = status_code
        self.content = content or b""
        self.headers = headers or {}

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")

    def json(self):
        if not self.content:
            raise ValueError("empty response")
        return jsonlib.loads(self.text)


# -- multipart ---------------------------------------------------------------

class _Chained(io.RawIOBase):
    """Reads several streams end to end, so an upload is never held in memory.

    A preview movie can be hundreds of megabytes; building the multipart body
    as one bytes object would mean holding all of it plus a copy.
    """

    def __init__(self, parts):
        self._parts = list(parts)
        self._current = None

    def readable(self):
        return True

    def read(self, size=-1):
        chunks = []
        remaining = size

        while remaining != 0:
            if self._current is None:
                if not self._parts:
                    break
                part = self._parts.pop(0)
                self._current = io.BytesIO(part) if isinstance(part, bytes) else part

            chunk = self._current.read(remaining if remaining and remaining > 0 else -1)
            if not chunk:
                if self._current is not None and not isinstance(self._current, io.BytesIO):
                    self._current.close()
                self._current = None
                continue

            chunks.append(chunk)
            if remaining > 0:
                remaining -= len(chunk)

        return b"".join(chunks)


def _multipart(file_path, field="file"):
    """``(body_stream, content_type, length)`` for one file upload."""
    boundary = uuid.uuid4().hex
    name = os.path.basename(file_path)
    mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    prefix = (
        "--%s\r\n"
        'Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
        "Content-Type: %s\r\n\r\n" % (boundary, field, name, mime)
    ).encode("utf-8")
    suffix = ("\r\n--%s--\r\n" % boundary).encode("utf-8")

    size = os.path.getsize(file_path)
    handle = open(file_path, "rb")
    stream = _Chained([prefix, handle, suffix])

    return (stream, "multipart/form-data; boundary=%s" % boundary,
            len(prefix) + size + len(suffix))


# -- backends ----------------------------------------------------------------

class RequestsTransport:
    name = "requests"

    def __init__(self, verify=True):
        self.verify = verify
        self.session = _requests.Session()
        self.session.verify = verify

    def request(self, method, url, headers=None, json=None, file=None,
                params=None, timeout=None):
        connect, read = timeout or (CONNECT_TIMEOUT, READ_TIMEOUT)
        url = with_params(url, params)
        arguments = {"headers": headers or {}, "timeout": (connect, read)}

        if json is not None:
            arguments["json"] = json
        handle = None
        try:
            if file is not None:
                handle = open(file, "rb")
                mime = mimetypes.guess_type(file)[0] or "application/octet-stream"
                arguments["files"] = {"file": (os.path.basename(file), handle, mime)}

            try:
                response = self.session.request(method, url, **arguments)
            except _requests.RequestException as error:
                raise TransportError(str(error))
        finally:
            if handle is not None:
                handle.close()

        return Response(response.status_code, response.content,
                        dict(response.headers))


class UrllibTransport:
    """The no-dependency path, for a DCC whose Python ships without requests."""

    name = "urllib"

    def __init__(self, verify=True):
        self.verify = verify
        if verify:
            try:
                import certifi
                self.context = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                self.context = ssl.create_default_context()
        else:
            self.context = ssl._create_unverified_context()

    def request(self, method, url, headers=None, json=None, file=None,
                params=None, timeout=None):
        _connect, read = timeout or (CONNECT_TIMEOUT, READ_TIMEOUT)
        url = with_params(url, params)
        headers = dict(headers or {})
        body = None

        if file is not None:
            body, content_type, length = _multipart(file)
            headers["Content-Type"] = content_type
            headers["Content-Length"] = str(length)
        elif json is not None:
            body = jsonlib.dumps(json).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=body, headers=headers,
                                         method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=read,
                                        context=self.context) as answer:
                return Response(answer.status, answer.read(),
                                dict(answer.headers))
        except urllib.error.HTTPError as error:
            # An error status is a response, not a failure to reach the server.
            return Response(error.code, error.read() or b"",
                            dict(error.headers or {}))
        except (urllib.error.URLError, OSError, ssl.SSLError) as error:
            raise TransportError(str(error))
        finally:
            if body is not None and hasattr(body, "close"):
                body.close()


def with_params(url, params):
    """A URL with a query string appended, skipping empty values."""
    if not params:
        return url
    pairs = [(k, v) for k, v in params.items() if v not in (None, "")]
    if not pairs:
        return url
    separator = "&" if "?" in url else "?"
    return url + separator + urllib.parse.urlencode(pairs, doseq=True)


def make_transport(verify=True, prefer=None):
    """The best transport available.

    ``prefer`` forces one by name, which is what the tests use to exercise the
    urllib path on a machine where requests is installed.
    """
    if prefer == "urllib" or _requests is None:
        return UrllibTransport(verify)
    if prefer in (None, "requests"):
        return RequestsTransport(verify)
    raise ValueError("unknown transport %r" % prefer)


def available():
    """Which backends this Python can offer."""
    return ("requests", "urllib") if _requests else ("urllib",)
