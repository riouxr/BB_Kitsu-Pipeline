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


# A ``key = "value"`` line, which is the only shape a root is ever written in.
_ASSIGNMENT = re.compile(r'^(\s*[\w.\-]+\s*=\s*)"(.*)"(\s*(?:#.*)?)$')

# The characters that make a backslash the start of a real TOML escape.
_ESCAPES = 'btnfr"\\uU'


def _escape_backslashes(value):
    r"""Double every backslash that is not already starting an escape.

    Scanned rather than substituted, because a pair has to be consumed
    together: a regex that looks one character ahead turns the already-correct
    ``E:\\Show`` into three backslashes, which is a different kind of broken.
    """
    out = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != '\\':
            out.append(char)
            index += 1
            continue

        following = value[index + 1] if index + 1 < len(value) else ''
        if following and following in _ESCAPES:
            out.append(char)
            out.append(following)
            index += 2
        else:
            out.append('\\\\')
            index += 1
    return ''.join(out)


def _windows_paths(block):
    r"""Let a Windows path be pasted into a brief exactly as it is copied.

    TOML reads a backslash in a double-quoted string as an escape, so
    ``work_root = "E:\Misery Loves Company"`` is not a path with a typo in
    it - it is a parse error at ``\M``, and the whole block is discarded.
    Nobody setting a project root should have to know that.

    Only ``key = "value"`` lines are touched, and only backslashes that do
    not already begin a real escape, so a brief written correctly in the
    first place parses to exactly the same thing.
    """
    fixed = []
    for line in block.splitlines():
        match = _ASSIGNMENT.match(line)
        if match:
            head, value, tail = match.group(1), match.group(2), match.group(3)
            line = '%s"%s"%s' % (head, _escape_backslashes(value), tail)
        fixed.append(line)
    return "\n".join(fixed)


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
        parsed = tomllib.loads(_windows_paths(block))
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


def problem(project):
    """The reason a project's [bb] block was ignored, or ''.

    Config deliberately swallows a bad brief - a broken block must not stop
    the tools loading - but swallowing it silently is how "Set a Work Root"
    ends up on screen for a project that plainly has one. This is what the
    message says instead.
    """
    description = (project or {}).get("description")
    if not extract(description):
        return ""
    try:
        parse(description)
    except BadBrief as error:
        return "the [bb] block in the Kitsu brief will not parse: %s" % error
    return ""
