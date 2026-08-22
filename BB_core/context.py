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

from dataclasses import asdict, dataclass, fields as dataclass_fields

from . import naming
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

    def is_complete(self):
        """True when there is enough here to build a name."""
        return all(self.as_fields().values())

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


# The old name, kept so nothing that still imports it breaks mid-refactor.
ShotContext = EntityContext
