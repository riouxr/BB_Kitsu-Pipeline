"""Version discovery and increment.

The rule the whole pipeline hangs on: a version number is read once, from the
files on disk or from the loaded scene, and then carried on the context. It is
never typed in twice and never re-derived from a path string further down.
"""

import re
from pathlib import Path

from . import naming
from .config import Config


# A marker appended to every publish comment, so a later "open the scene
# behind this picture" can recover the answer. Kitsu's own preview revision
# counts publishes, not saves - several published angles off one saved scene
# is a revision bump with no matching version bump - so the two numbers
# drift apart under completely normal use, and nothing else on a comment
# reliably ties it back to a local file: the comment text itself is what an
# artist writes their actual note into, and gets overwritten immediately.
# Double brackets and a fixed `v` prefix keep this unlikely to collide with
# an artist's own text mentioning a version in passing.
_VERSION_TAG = re.compile(r"\[\[v(\d+)\]\]")

# A second, distinct marker for "this version was flagged master", posted as
# its own comment by the Kitsu page's "Set as Master" button - a durable,
# human-readable record of what was master and when, kept apart from the
# per-publish version tag above so parsing one can never pick up the other.
_MASTER_TAG = re.compile(r"\[\[master:v(\d+)\]\]")


def format_master_tag(version):
    """The marker recording a version as master, e.g. ``"[[master:v5]]"``."""
    return "[[master:v%d]]" % int(version)


def parse_master_tag(text):
    """The version out of a master-flag comment, or None without one."""
    match = _MASTER_TAG.search(text or "")
    return int(match.group(1)) if match else None


def format_version_tag(version):
    """The marker for one version, e.g. ``"[[v003]]"``."""
    return "[[v%03d]]" % int(version)


def parse_version_tag(text):
    """The version out of a comment carrying a marker, or None without one."""
    match = _VERSION_TAG.search(text or "")
    return int(match.group(1)) if match else None


def tag_comment(comment, version):
    """*comment* with its version marker appended.

    Replaces a marker already there rather than stacking a second one, so
    retagging (should that ever happen) cannot leave two conflicting markers
    for a parser to pick between.
    """
    base = _VERSION_TAG.sub("", comment or "").rstrip()
    tag = format_version_tag(version)
    return "%s\n\n%s" % (base, tag) if base else tag


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

    suffix = "." + ext.lstrip(".")

    found = []
    for entry in directory.iterdir():
        if not entry.is_file() or entry.suffix.lower() != suffix.lower():
            continue
        parsed = naming.parse(entry.name, config)
        if not parsed:
            continue
        if not naming.names_the_same(parsed, fields, config):
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
