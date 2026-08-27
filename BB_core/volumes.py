r"""The same root, spelled the way this machine mounts it.

One Kitsu brief serves a whole studio, so a project root is written once -
``work_root = "E:\Misery Loves Company"`` - and every machine has to make
sense of it. On Windows that is a drive letter. On macOS the same disk is
under ``/Volumes`` with whatever name it was formatted with, and the
backslashes are not separators at all: ``PurePosixPath`` reads the whole
thing as a single filename with slashes in it, so a path that merely looks
wrong is actually one component.

The translation is machine-local and lives in the settings file, never in
Kitsu:

    "volumes": {"E:": "/Volumes/Misery", "I:": "/Volumes/I 4TB_Externe"}

Read in both directions, so the same table serves a Mac reading a brief
written on Windows and a Windows box reading one written on a Mac. Nothing
here touches the disk: a root for a volume that is not mounted yet still
translates, and fails later where a missing folder is reported properly.
"""

import ntpath
import posixpath
import re
import sys

# ``E:`` or ``E:\``: a drive letter is the one part of a Windows path that
# cannot survive being moved to another platform.
_DRIVE = re.compile(r"^([A-Za-z]):[\\/]?")


def _settings_map():
    from . import settings
    table = settings.get("volumes") or {}
    return table if isinstance(table, dict) else {}


def _split(value):
    """A path split into components, whichever separator it was written with."""
    return [part for part in re.split(r"[\\/]+", value) if part]


def _join(parts, posix):
    joiner = posixpath if posix else ntpath
    return joiner.sep.join(parts)


def localise(value, mapping=None, platform=None):
    r"""*value* as this machine should spell it.

    Returns the value unchanged when nothing in the table applies, so a root
    that is already right for this platform - or a UNC path, or a share this
    machine mounts identically - is never mangled.
    """
    value = (value or "").strip()
    if not value:
        return value

    mapping = _settings_map() if mapping is None else mapping
    platform = sys.platform if platform is None else platform
    posix = not platform.startswith("win")

    drive = _DRIVE.match(value)

    if posix:
        if not drive:
            return value
        mount = _lookup(mapping, drive.group(1))
        if not mount:
            # Returned untouched on purpose. Dropping the drive letter would
            # turn E:\Show into /Show - a real-looking root that is not the
            # one anybody meant, failing later and somewhere else. Left as it
            # is, `unresolved` can name the letter that needs a mapping.
            return value
        rest = _split(value[drive.end():])
        return posixpath.join(mount.rstrip("/"), *rest) if rest else mount

    # Windows, given a posix root: find the mount this table knows.
    if drive:
        # Already a drive path; only the separators may need turning round.
        return value.replace("/", ntpath.sep)

    for letter, mount in mapping.items():
        mount = (mount or "").rstrip("/")
        if not mount:
            continue
        if value.lower() == mount.lower():
            return _drive_of(letter) + ntpath.sep
        if value.lower().startswith(mount.lower() + "/"):
            rest = _split(value[len(mount):])
            return ntpath.join(_drive_of(letter) + ntpath.sep, *rest)

    return value


def _lookup(mapping, letter):
    """The mount for a drive letter, however the table spells the key."""
    for key, mount in (mapping or {}).items():
        cleaned = str(key).strip().rstrip(":\\/")
        if cleaned.lower() == letter.lower():
            return str(mount).strip()
    return ""


def _drive_of(key):
    return str(key).strip().rstrip(":\\/").upper() + ":"


def describe(mapping=None, platform=None):
    """A one-line summary of the table, for a settings panel to show."""
    mapping = _settings_map() if mapping is None else mapping
    if not mapping:
        return ""
    pairs = ", ".join("%s -> %s" % (_drive_of(key), mount)
                      for key, mount in sorted(mapping.items()))
    return "volumes: %s" % pairs


def unresolved(value, mapping=None, platform=None):
    """The drive letter *value* needs a mapping for, or ''.

    A root written on Windows means nothing on a Mac until this machine is
    told where that disk is mounted. Naming the letter is what makes the
    message actionable instead of "no such folder".
    """
    value = (value or "").strip()
    platform = sys.platform if platform is None else platform
    if not value or platform.startswith("win"):
        return ""

    drive = _DRIVE.match(value)
    if not drive:
        return ""

    mapping = _settings_map() if mapping is None else mapping
    return "" if _lookup(mapping, drive.group(1)) else _drive_of(drive.group(1))
