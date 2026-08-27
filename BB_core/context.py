"""The one object every part of the pipeline passes around.

An :class:`EntityContext` is "which thing, which task, which version" together
with the Kitsu ids needed to publish back. Every name and every path is
derived from it, and the render side will derive its parallel streams from the
same instance - so a render can never be filed under a different version than
the scene that produced it.

A Kitsu project holds two trees and the pipeline has to work in both, so the
context names them generically:

    entity_type   group           entity
    ------------  --------------  --------------
    "shot"        sequence        shot
    "asset"       asset type      asset

That is what lets one naming template, one path builder and one publish call
serve both, instead of a shot code path and an asset code path drifting apart.

The context carries both display names (for building paths) and Kitsu ids (for
the API). Keeping them together is what lets the publish call read the version
from the same object that named the render, instead of re-parsing a path.
"""

import re

from dataclasses import asdict, dataclass, fields as dataclass_fields

from . import naming

# The tokens a template asks for, so a context can be checked against what
# will actually be built from it rather than against every field there is.
_TOKENS = re.compile(r"\{([a-z_]+)[^}]*\}")


def _naming_templates(config):
    return list(naming.base_templates(config))


def _path_templates(config):
    keys = ("work_dir_shot", "work_dir_asset", "work_file",
            "render_dir_shot", "render_dir_asset", "render_stem")
    return [config.paths.get(key) or "" for key in keys]
from .config import Config

SCENE_KEY = "BB_pipeline"
SCHEMA = 2

SHOT = "shot"
ASSET = "asset"
ENTITY_TYPES = (SHOT, ASSET)


@dataclass
class EntityContext:
    entity_type: str = SHOT

    project: str = ""
    group: str = ""
    entity: str = ""
    task: str = ""

    project_id: str = ""
    group_id: str = ""
    entity_id: str = ""
    task_id: str = ""
    task_type_id: str = ""

    # The Kitsu department the task type belongs to. Not part of any name by
    # default, but it is what each DCC filters its task list on.
    department: str = ""

    version: int = 0
    server: str = ""
    schema: int = SCHEMA

    def as_fields(self):
        """The mapping the naming templates consume."""
        return {name: getattr(self, name) for name in naming.FIELDS}

    def is_complete(self, config=None):
        """True when there is enough here to build a name and a path.

        Asks the templates rather than demanding every field. The default
        scheme names a file after its entity and its version and puts the
        rest in folders, so requiring a project that nothing spells out
        would refuse contexts that are perfectly usable - which is exactly
        what happened to a file recovered from its path.
        """
        config = config or Config()
        needed = set()
        for template in _naming_templates(config) + _path_templates(config):
            needed.update(_TOKENS.findall(template))

        fields = self.as_fields()
        needed &= set(fields)
        if not needed:
            needed = {"entity"}
        return all(fields.get(name) for name in needed)

    @property
    def is_asset(self):
        return self.entity_type == ASSET

    def base(self, config=None):
        return naming.format_base(self.as_fields(), config or Config(),
                                  self.entity_type)

    def versioned(self, version=None, config=None):
        version = self.version if version is None else version
        return naming.format_versioned(self.as_fields(), version,
                                       config or Config(), self.entity_type)

    def at_version(self, version):
        """A copy pinned to another version; the original is untouched."""
        clone = EntityContext(**asdict(self))
        clone.version = int(version)
        return clone

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        """Rebuild from stored data, ignoring keys this version does not know.

        Tolerant on purpose: a .blend stamped by a newer build of the add-on
        should still open in an older one rather than raising on an unexpected
        key. Schema 1 stamps, written before assets were supported, named the
        two levels sequence/shot and are translated rather than dropped.
        """
        data = dict(data or {})

        if "sequence" in data or "shot" in data:
            data.setdefault("group", data.pop("sequence", ""))
            data.setdefault("entity", data.pop("shot", ""))
            data.setdefault("group_id", data.pop("sequence_id", ""))
            data.setdefault("entity_id", data.pop("shot_id", ""))
            data.setdefault("entity_type", SHOT)

        known = {f.name for f in dataclass_fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_filename(cls, name, config=None):
        """Best-effort context from a filename alone.

        The fallback for a file that was never stamped - it recovers the
        names and version but no Kitsu ids, and cannot tell a shot from an
        asset, so the caller still has to resolve those against the server
        before it can publish.
        """
        parsed = naming.parse(name, config or Config())
        if not parsed:
            return None
        return cls(
            project=parsed.get("project", ""),
            group=parsed.get("group", ""),
            entity=parsed.get("entity", ""),
            task=parsed.get("task", ""),
            version=parsed.get("version", 0),
        )


    @classmethod
    def from_path(cls, path, config=None):
        """Best-effort context from where a file sits, plus what it is called.

        The filename carries the entity and the version. Everything else -
        the project's group, the entity again, the task - is in the folders
        above it, which is exactly why the name stopped repeating them.

        The work_dir template says which folder means what, so this reads the
        trailing folders against it rather than guessing by position. Returns
        None when the name does not fit the scheme at all; a name that fits
        but sits somewhere unexpected still yields what the name gave.
        """
        from pathlib import PurePath

        from . import naming

        config = config or Config()
        name = PurePath(str(path)).name
        parsed = naming.parse(name, config)
        if not parsed:
            return None

        fields = {"project": "", "group": "", "entity": "", "task": ""}
        fields.update({k: v for k, v in parsed.items() if k in fields})

        folders = list(PurePath(str(path)).parent.parts)
        entity_type = ""

        for key, kind in (("work_dir_asset", "asset"), ("work_dir_shot", "shot")):
            template = config.paths.get(key) or ""
            tokens = [part for part in template.replace("\\", "/").split("/") if part]
            if not tokens or len(tokens) > len(folders):
                continue

            tail = folders[-len(tokens):]
            found = {}
            fits = True
            for token, folder in zip(tokens, tail):
                if token.startswith("{") and token.endswith("}"):
                    found[token[1:-1]] = folder
                elif token.lower() != folder.lower():
                    # A literal in the template - "assets" - that has to be
                    # there for this to be that kind of entity.
                    fits = False
                    break
            if not fits:
                continue

            # The entity in the name has to agree with the entity in the
            # path, or this is a file that merely happens to sit here.
            named = fields.get("entity") or found.get("entity", "")
            if found.get("entity") and named and found["entity"].lower() != named.lower():
                continue

            for key_name in ("group", "entity", "task"):
                if found.get(key_name) and not fields.get(key_name):
                    fields[key_name] = found[key_name]
            entity_type = kind
            break

        return cls(
            project=fields.get("project", ""),
            group=fields.get("group", ""),
            entity=fields.get("entity", ""),
            task=fields.get("task", ""),
            entity_type=entity_type,
            version=parsed.get("version", 0),
        )


# The old name, kept so nothing that still imports it breaks mid-refactor.
ShotContext = EntityContext
