"""Version discovery and increment.

The rule the whole pipeline hangs on: a version number is read once, from the
files on disk or from the loaded scene, and then carried on the context. It is
never typed in twice and never re-derived from a path string further down.
"""

import re
from pathlib import Path

from . import naming
from .config import Config


def version_from_name(name, config=None):
    """The version encoded in a filename, or None if it carries no context."""
    parsed = naming.parse(name, config or Config())
    return parsed["version"] if parsed else None


def existing_versions(directory, fields, ext, config=None):
    """Every version of one shot/task found in ``directory``.

    Returns a sorted list of ``(version, Path)``. Files that do not match the
    naming scheme, or that match a different shot or task, are ignored - a
    stray ``old_comp.blend`` must not push the next version forward.
    """
    config = config or Config()
    directory = Path(directory)
    if not directory.is_dir():
        return []

    wanted = naming.format_base(fields, config)
    suffix = "." + ext.lstrip(".")

    found = []
    for entry in directory.iterdir():
        if not entry.is_file() or entry.suffix.lower() != suffix.lower():
            continue
        parsed = naming.parse(entry.name, config)
        if not parsed:
            continue
        if naming.format_base(parsed, config).lower() != wanted.lower():
            continue
        found.append((parsed["version"], entry))

    return sorted(found, key=lambda item: item[0])


def latest_version(directory, fields, ext, config=None):
    """The highest version on disk as ``(version, Path)``, or None."""
    versions = existing_versions(directory, fields, ext, config)
    return versions[-1] if versions else None


def next_version(directory, fields, ext, config=None):
    """The version number a new file should take.

    One past the highest that exists, starting at 1. Deliberately not "count
    of files plus one" - deleting v002 must not hand v003 out twice.
    """
    latest = latest_version(directory, fields, ext, config)
    return (latest[0] + 1) if latest else 1


def bump(path, config=None):
    """The next-version sibling of an existing file path.

    Used by "increment and save": the new name comes from the file that is
    open, so the scene on disk and the version in the name cannot disagree.
    """
    path = Path(path)
    parsed = naming.parse(path.name, config or Config())
    if not parsed:
        raise ValueError("not a pipeline filename: %s" % path.name)

    fields = {k: parsed[k] for k in naming.FIELDS if k in parsed}
    ext = path.suffix.lstrip(".")
    version = next_version(path.parent, fields, ext, config)
    return path.parent / (naming.format_versioned(fields, version, config) + path.suffix), version
