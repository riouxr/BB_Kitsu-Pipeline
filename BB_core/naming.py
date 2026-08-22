"""The canonical studio naming scheme.

One function builds every name the pipeline produces, and one function parses
a name back into its parts. Two rules keep those two honest:

* Field values are sanitized to a character set that excludes the separator,
  so ``VIL_FF9_0070_precomp3d_v003`` splits back into exactly four fields plus
  a version and never guesses.
* The two middle fields are ``group`` and ``entity`` - sequence/shot for a
  shot, asset type/asset for an asset - so one template names both trees.
* The version is formatted from an integer with the configured padding, so
  three-digit and four-digit versions cannot drift apart between the folder
  name and the filename the way they did in the earlier tools.
"""

import re

from .config import Config

# Fields the templates may reference, in the order they appear in the default
# scheme. Parsing walks these in template order, so adding a field is a config
# change plus an entry here.
FIELDS = ("project", "group", "entity", "task")

_TOKEN = re.compile(r"\{(\w+)\}")


def sanitize(value, config=None):
    """A field value reduced to the configured character set.

    Runs of illegal characters collapse to a single replacement character, and
    leading/trailing replacements are trimmed, so ``"FF9 / 0070 "`` becomes
    ``"FF9-0070"`` rather than ``"FF9--0070-"``.

    ``naming.case`` forces the result upper or lower; unset, names are left
    spelled the way production spells them.
    """
    config = config or Config()
    allowed = config.naming["allowed"]
    replacement = config.naming["replacement"]

    cleaned = re.sub(r"[^%s]+" % allowed, replacement, str(value))
    if replacement:
        cleaned = cleaned.strip(replacement)

    case = config.naming.get("case")
    if case == "lower":
        cleaned = cleaned.lower()
    elif case == "upper":
        cleaned = cleaned.upper()
    return cleaned


def format_version(version, config=None):
    """``3`` -> ``"003"`` at the configured padding."""
    config = config or Config()
    return str(int(version)).zfill(int(config.naming["version_padding"]))


def base_template(config, entity_type=None):
    """The stem template, per entity type when the config distinguishes them.

    A Kitsu file tree names shots and assets separately, and they are usually
    structurally identical - so they collapse to one ``base``. They only stay
    apart when a studio really does name the two trees differently.
    """
    if entity_type:
        specific = config.naming.get("base_%s" % entity_type)
        if specific:
            return specific
    return config.naming["base"]


def base_templates(config):
    """Every stem template the config defines, most specific first."""
    seen = []
    for key in ("base_shot", "base_asset", "base"):
        template = config.naming.get(key)
        if template and template not in seen:
            seen.append(template)
    return seen


def format_base(fields, config=None, entity_type=None):
    """The unversioned stem, e.g. ``VIL_FF9_0070_precomp3d``.

    ``fields`` is any mapping carrying the template's field names; an
    :class:`~BB_core.context.EntityContext` satisfies it via ``as_fields()``.
    """
    config = config or Config()
    template = base_template(config, entity_type)

    values = {}
    for name in _TOKEN.findall(template):
        value = fields.get(name)
        if not value:
            raise ValueError("naming: missing field %r for template %r" % (name, template))
        values[name] = sanitize(value, config)

    return template.format(**values)


def format_versioned(fields, version, config=None, entity_type=None):
    """The full versioned name, e.g. ``VIL_FF9_0070_precomp3d_v003``."""
    config = config or Config()
    return config.naming["versioned"].format(
        base=format_base(fields, config, entity_type),
        version=format_version(version, config),
    )


def _parse_pattern(config, base):
    """A regex built from the configured templates, with named groups.

    Built from the templates rather than hardcoded so a studio that reorders
    or drops a field still gets working round-trips.
    """
    allowed = config.naming["allowed"]
    versioned = config.naming["versioned"]

    def expand(template, mapping):
        out = []
        index = 0
        for match in _TOKEN.finditer(template):
            out.append(re.escape(template[index:match.start()]))
            out.append(mapping(match.group(1)))
            index = match.end()
        out.append(re.escape(template[index:]))
        return "".join(out)

    field_pattern = expand(base, lambda name: r"(?P<%s>[%s]+)" % (name, allowed))
    full = expand(versioned, lambda name: {
        "base": field_pattern,
        "version": r"(?P<version>\d+)",
    }[name])

    return re.compile("^" + full + "$", re.IGNORECASE)


def parse(name, config=None):
    """Split a versioned name back into its fields, or None if it does not fit.

    Any extension is stripped first, so both ``..._v003`` and
    ``..._v003.blend`` parse. Frame-numbered renders (``..._v003.0001.exr``)
    parse too - only the trailing extension is dropped, and the frame number
    is not a field.
    """
    config = config or Config()
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", str(name))
    stem = re.sub(r"\.\d+$", "", stem)

    # Every stem template gets a try. A config that names shots and assets
    # differently has two, and a name only has to match one of them.
    for template in base_templates(config):
        match = _parse_pattern(config, template).match(stem)
        if match:
            parsed = match.groupdict()
            parsed["version"] = int(parsed["version"])
            return parsed

    return None
