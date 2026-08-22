"""Pipeline settings written into a Kitsu project's brief.

Kitsu has no field for a working path, and its ``file_tree`` is a JSON column
with no interface at all - you have to PUT it through the API. The project
*description*, which Kitsu shows as the Brief, is a plain text box anybody can
edit in the browser. That makes it the one place a producer can set a show's
root without a developer.

So a ``[bb]`` block in the brief is read as configuration:

    Pizza Hunt - second season. Delivery 12 December.

    [bb]
    work_root = "I:/PizzaHunt"
    render_root = "P:/PizzaHunt/renders"

    [bb.naming]
    version_padding = 4

Bare keys are paths, because roots are the whole point of the feature.
Sub-tables address the other config sections by name, so anything the config
file can say the brief can say too.

Only the block is read. The prose around it is left alone, and a brief with no
block is simply a brief.
"""

import re
import tomllib

# Kitsu's brief is a plain textarea today, but it has been rich text before -
# so markup is stripped rather than fed to the TOML parser as syntax errors.
#
# Block-level tags become newlines instead of vanishing. Dropping them would
# weld "Notes" and "[bb]" into one line and the block would stop being
# found, because the marker has to start a line.
_BLOCK_TAG = re.compile(
    r"</?(?:p|div|br|pre|li|ul|ol|h[1-6]|tr|blockquote)\b[^>]*>",
    re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_ENTITIES = (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
             ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"))

MARKER = "[bb]"

# Config sections a sub-table may address. Anything else is ignored rather
# than merged, so a typo cannot quietly invent a new setting.
SECTIONS = ("paths", "naming", "streams", "dcc", "projects")


class BadBrief(Exception):
    """The [bb] block is there but is not valid TOML."""


def _plain(text):
    text = _BLOCK_TAG.sub("\n", str(text or ""))
    text = _TAG.sub("", text)
    for entity, char in _ENTITIES:
        text = text.replace(entity, char)
    return text


def extract(description):
    """The raw TOML of the ``[bb]`` block in a brief, or ''.

    Reads from the marker to the next top-level table that is not part of the
    block, so prose after it is not swallowed.
    """
    text = _plain(description)
    lines = text.splitlines()

    start = None
    for index, line in enumerate(lines):
        if line.strip().lower() == MARKER:
            start = index
            break
    if start is None:
        return ""

    collected = [lines[start]]
    for line in lines[start + 1:]:
        stripped = line.strip()
        # Another top-level table ends the block, unless it belongs to it.
        if (stripped.startswith("[") and stripped.endswith("]")
                and not stripped.lower().startswith("[bb")):
            break
        collected.append(line)

    return "\n".join(collected).strip()


def parse(description):
    """Config overrides from a brief, or None when it carries none.

    Raises :class:`BadBrief` when the block exists but will not parse - a
    typo in a root is worth reporting, because silently ignoring it would
    write files somewhere unexpected.
    """
    block = extract(description)
    if not block:
        return None

    try:
        parsed = tomllib.loads(block)
    except Exception as error:
        raise BadBrief(str(error))

    settings = parsed.get("bb") or {}
    if not isinstance(settings, dict):
        return None

    overrides = {}
    paths = {}

    for key, value in settings.items():
        if isinstance(value, dict):
            if key in SECTIONS:
                overrides[key] = value
            continue
        # A bare key is a path; that is what people come here to set.
        paths[key] = value

    if paths:
        overrides["paths"] = {**overrides.get("paths", {}), **paths}

    return overrides or None


def describe(project):
    """A one-line summary of what a project's brief contributed."""
    description = (project or {}).get("description")
    if not extract(description):
        return ""
    try:
        overrides = parse(description)
    except BadBrief as error:
        return "the [bb] block in the Kitsu brief is not valid TOML: %s" % error

    if not overrides:
        return "the [bb] block in the Kitsu brief is empty"

    roots = overrides.get("paths", {})
    if roots:
        return "roots from the Kitsu brief: %s" % ", ".join(sorted(roots))
    return "settings from the Kitsu brief"
