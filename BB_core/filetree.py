"""Reading a project's layout out of Kitsu.

Kitsu projects carry a ``file_tree``: a JSON template describing where working
files and outputs live. When one is set, it is the studio's answer to "where
do the files go", and every DCC should be reading the same answer - so this
translates it into the templates the rest of the core already speaks, and the
local config becomes the fallback rather than the authority.

Deliberately a *translation*, not a call to Kitsu's
``/tasks/{id}/working-file-path``. That endpoint is built around Kitsu's
working-file records and revisions, which would make Kitsu the version
authority instead of the files on disk, and it costs a round trip per path.
Reading the template once and building paths locally keeps versioning where it
is and keeps path building instant.

What Kitsu's tree does *not* carry is the version. Its endpoint returns a
folder and a stem with no revision in either, so ``_v003`` and the extension
stay ours to add.

Zou's tokens map onto the generic context fields:

    <Project>     project        <Shot>       entity  (shots)
    <Sequence>    group          <Asset>      entity  (assets)
    <AssetType>   group          <TaskType>   task
    <Department>  department     <OutputType> stream
"""

import re

# Case is handled differently on purpose. Zou lowercases everything unless the
# tree says "uppercase", with no option to leave names alone - so a shot Kitsu
# calls sh01 on an asset called Knife would come back flattened. Names are
# left as production spells them unless the tree explicitly asks otherwise.
STYLES = {"lowercase": "lower", "uppercase": "upper"}

SHOT_TOKENS = {
    "Project": "project",
    "Sequence": "group",
    "Shot": "entity",
    "TaskType": "task",
    "Task": "task",
    "Department": "department",
    "OutputType": "stream",
}

ASSET_TOKENS = dict(SHOT_TOKENS)
ASSET_TOKENS.update({"AssetType": "group", "Asset": "entity"})

# Tokens Zou knows that this pipeline has no field for. A tree using one is
# reported rather than silently producing a path with a hole in it.
UNSUPPORTED = ("Episode", "Scene", "Software", "Representation", "Name")

_TOKEN = re.compile(r"<(\w+)>")


class UnsupportedTree(Exception):
    """The Kitsu tree uses something this pipeline cannot express yet."""


def _translate(template, tokens, where):
    if not template:
        return None

    def swap(match):
        name = match.group(1)
        if name in tokens:
            return "{%s}" % tokens[name]
        if name in UNSUPPORTED:
            raise UnsupportedTree(
                "%s uses <%s>, which the pipeline has no field for" % (where, name))
        raise UnsupportedTree("%s uses an unknown token <%s>" % (where, name))

    return _TOKEN.sub(swap, template.strip("/"))


def _style(section):
    for part in ("file_name", "folder_path"):
        style = (section.get(part) or {}).get("style")
        if style in STYLES:
            return STYLES[style]
    return ""


def translate(project):
    """Config overrides for a project's Kitsu file tree, or None.

    Returns a dict shaped like the config file - ``{"paths": {...},
    "naming": {...}}`` - ready to merge over the defaults. None when the
    project has no tree, which is the normal case and not an error.
    """
    tree = (project or {}).get("file_tree")
    if not isinstance(tree, dict):
        return None

    working = tree.get("working") or {}
    folders = working.get("folder_path") or {}
    names = working.get("file_name") or {}

    paths = {}
    naming = {}

    shot_dir = _translate(folders.get("shot"), SHOT_TOKENS, "shot folder_path")
    asset_dir = _translate(folders.get("asset"), ASSET_TOKENS, "asset folder_path")
    if shot_dir:
        paths["work_dir_shot"] = shot_dir
    if asset_dir:
        paths["work_dir_asset"] = asset_dir

    shot_name = _translate(names.get("shot"), SHOT_TOKENS, "shot file_name")
    asset_name = _translate(names.get("asset"), ASSET_TOKENS, "asset file_name")

    # The two collapse to one template when they are structurally parallel,
    # which they normally are - <Sequence>/<Shot> and <AssetType>/<Asset> both
    # become {group}/{entity}. Only keep them apart when they really differ.
    if shot_name and asset_name and shot_name == asset_name:
        naming["base"] = shot_name
    else:
        if shot_name:
            naming["base_shot"] = shot_name
        if asset_name:
            naming["base_asset"] = asset_name

    style = _style(working)
    if style:
        naming["case"] = style

    if not paths and not naming:
        return None

    overrides = {}
    if paths:
        overrides["paths"] = paths
    if naming:
        overrides["naming"] = naming
    return overrides


def describe(project):
    """A one-line summary of where a project's tree came from."""
    tree = (project or {}).get("file_tree")
    if not isinstance(tree, dict):
        return "no file tree in Kitsu - using the local config"
    try:
        translate(project)
    except UnsupportedTree as error:
        return "Kitsu file tree ignored: %s" % error
    return "layout from the Kitsu file tree"
