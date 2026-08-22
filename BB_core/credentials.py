"""Password storage, without a dependency.

Blender does not ship ``keyring`` and the extension has no wheels, so on
Windows this talks to the Credential Manager directly through ctypes. It reads
and writes the same Generic credentials keyring's Windows backend does -
target name ``service/username``, password as a UTF-16LE blob - so a password
saved here is visible to the standalone PySide6 tools and vice versa.

Elsewhere it defers to ``keyring`` if it happens to be importable, and
otherwise reports that no store is available. Nothing in this module ever
writes a password to a file: the earlier Resolve tool kept one in plain text
in ``~/.kitsu_resolve.json``, and that is the mistake this exists to avoid.
"""

import sys

SERVICE = "BBPipeline"

_WINDOWS = sys.platform == "win32"

if _WINDOWS:
    import ctypes
    from ctypes import wintypes

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD)]

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", _FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    _advapi32.CredReadW.restype = wintypes.BOOL
    _advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    _advapi32.CredWriteW.restype = wintypes.BOOL
    _advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _advapi32.CredDeleteW.restype = wintypes.BOOL
    _advapi32.CredFree.argtypes = [ctypes.c_void_p]
    _advapi32.CredFree.restype = None


def _target(service, username):
    return "%s/%s" % (service, username)


def available():
    """True when there is somewhere safe to put a password."""
    if _WINDOWS:
        return True
    try:
        import keyring  # noqa: F401
        return True
    except ImportError:
        return False


def get_password(username, service=SERVICE):
    """The stored password, or None if nothing is stored for this user."""
    if not username:
        return None

    if not _WINDOWS:
        try:
            import keyring
            return keyring.get_password(service, username)
        except ImportError:
            return None

    pointer = ctypes.POINTER(_CREDENTIAL)()
    ok = _advapi32.CredReadW(_target(service, username), CRED_TYPE_GENERIC, 0,
                             ctypes.byref(pointer))
    if not ok:
        if ctypes.get_last_error() != ERROR_NOT_FOUND:
            pass  # Any other failure is reported the same way: nothing stored.
        return None

    try:
        credential = pointer.contents
        blob = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return blob.decode("utf-16-le")
    finally:
        _advapi32.CredFree(pointer)


def set_password(username, password, service=SERVICE):
    """Store a password. Returns True when it actually landed somewhere."""
    if not username or password is None:
        return False

    if not _WINDOWS:
        try:
            import keyring
            keyring.set_password(service, username, password)
            return True
        except ImportError:
            return False

    blob = password.encode("utf-16-le")
    credential = _CREDENTIAL(
        Flags=0,
        Type=CRED_TYPE_GENERIC,
        TargetName=_target(service, username),
        Comment="BB Kitsu pipeline Kitsu login",
        LastWritten=_FILETIME(),
        CredentialBlobSize=len(blob),
        CredentialBlob=ctypes.cast(ctypes.create_string_buffer(blob, len(blob)),
                                   ctypes.POINTER(ctypes.c_char)),
        Persist=CRED_PERSIST_LOCAL_MACHINE,
        AttributeCount=0,
        Attributes=None,
        TargetAlias=None,
        UserName=username,
    )
    return bool(_advapi32.CredWriteW(ctypes.byref(credential), 0))


def delete_password(username, service=SERVICE):
    """Forget a stored password; True if one was there to forget."""
    if not username:
        return False

    if not _WINDOWS:
        try:
            import keyring
            keyring.delete_password(service, username)
            return True
        except Exception:
            return False

    return bool(_advapi32.CredDeleteW(_target(service, username), CRED_TYPE_GENERIC, 0))
