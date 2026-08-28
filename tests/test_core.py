"""Core tests.

Plain unittest, no pytest: these have to be runnable inside Blender's bundled
Python as well as a normal one, and Blender ships neither pytest nor a way to
add it without wheels.

    python -m unittest discover -s tests -v
"""

import ast
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from BB_core import (brief, filetree, frames, naming,  # noqa: E402
                       versioning, volumes, workfiles)
from BB_core.config import Config  # noqa: E402
from BB_core.context import EntityContext  # noqa: E402


def sample_context(version=3):
    return EntityContext(
        entity_type="shot",
        project="VIL", group="FF9", entity="0070", task="precomp3d",
        project_id="p-1", group_id="s-1", entity_id="sh-1", task_id="t-1",
        version=version,
    )


def sample_asset(version=1):
    return EntityContext(
        entity_type="asset",
        project="VIL", group="Prop", entity="knife", task="Modeling",
        project_id="p-1", group_id="at-1", entity_id="a-1", task_id="t-2",
        version=version,
    )


class TestNaming(unittest.TestCase):
    def test_studio_scheme(self):
        self.assertEqual(sample_context().versioned(), "0070_v003")

    def test_base_has_no_version(self):
        self.assertEqual(sample_context().base(), "0070")

    def test_sanitize_keeps_underscore_free(self):
        # The separator must never appear inside a field, or parsing is
        # ambiguous. A space becomes the replacement character, not "_".
        self.assertEqual(naming.sanitize("FF9 0070"), "FF9-0070")
        self.assertEqual(naming.sanitize("shot / 010 "), "shot-010")
        self.assertEqual(naming.sanitize("a???b"), "a-b")

    LONG = None

    @classmethod
    def long_scheme(cls):
        """A config spelling the whole context into the name.

        Still supported and still tested: the default stopped repeating what
        the folders say, but a studio that passes files around loose can put
        the long template back, and it has to keep working.
        """
        if cls.LONG is None:
            cls.LONG = Config()
            cls.LONG.naming["base"] = "{project}_{group}_{entity}_{task}"
        return cls.LONG

    def test_round_trip(self):
        context = sample_context(12)
        parsed = naming.parse(context.versioned())
        self.assertEqual(parsed["entity"], "0070")
        self.assertEqual(parsed["version"], 12)

    def test_the_long_scheme_still_round_trips(self):
        config = self.long_scheme()
        context = sample_context(12)
        parsed = naming.parse(context.versioned(config=config), config)
        self.assertEqual(parsed["project"], "VIL")
        self.assertEqual(parsed["group"], "FF9")
        self.assertEqual(parsed["entity"], "0070")
        self.assertEqual(parsed["task"], "precomp3d")
        self.assertEqual(parsed["version"], 12)

    def test_round_trip_survives_sanitizing(self):
        context = EntityContext(project="Villa Project", group="FF 9",
                               entity="00 70", task="precomp 3d", version=1)
        parsed = naming.parse(context.versioned())
        self.assertEqual(parsed["entity"], "00-70")
        self.assertEqual(parsed["version"], 1)

    def test_parse_ignores_extension_and_frame(self):
        self.assertEqual(naming.parse("0070_v004.blend")["version"], 4)
        self.assertEqual(naming.parse("0070_v004.0101.exr")["version"], 4)

    def test_parse_rejects_foreign_names(self):
        self.assertIsNone(naming.parse("old comp backup.blend"))
        self.assertIsNone(naming.parse("0070.blend"))

    def test_padding_is_uniform(self):
        # The old tools mixed v003 and v0004 between the folder and the file;
        # one padding setting now drives both.
        self.assertEqual(naming.format_version(4), "004")
        context = sample_context(4)
        self.assertIn("v004", str(workfiles.work_file(
            context, "blender",
            config=Config().with_roots(work_root="X:/work", render_root="X:/render"))))

    def test_missing_field_is_an_error(self):
        # Whatever the template asks for has to be there. The default names
        # only the entity, so that is the one that cannot be blank.
        with self.assertRaises(ValueError):
            EntityContext(project="VIL", group="FF9", task="comp").base()

    def test_the_long_scheme_still_demands_every_field(self):
        config = self.long_scheme()
        with self.assertRaises(ValueError):
            EntityContext(project="VIL", group="FF9",
                          entity="0070").base(config=config)


class TestVersioning(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.fields = sample_context().as_fields()

    def tearDown(self):
        self._tmp.cleanup()

    def touch(self, name):
        (self.root / name).write_text("")

    def test_empty_folder_starts_at_one(self):
        self.assertEqual(versioning.next_version(self.root, self.fields, "blend"), 1)

    def test_next_is_one_past_highest(self):
        for version in (1, 2, 3):
            self.touch("0070_v%03d.blend" % version)
        self.assertEqual(versioning.next_version(self.root, self.fields, "blend"), 4)

    def test_gap_does_not_reissue_a_version(self):
        # v002 deleted: the next file must still be v004, not v003 again.
        self.touch("0070_v001.blend")
        self.touch("0070_v003.blend")
        self.assertEqual(versioning.next_version(self.root, self.fields, "blend"), 4)

    def test_foreign_files_are_ignored(self):
        self.touch("0070_v001.blend")
        self.touch("random_backup.blend")
        self.touch("VIL_FF9_0070_lighting_v009.blend")   # different task
        self.touch("VIL_FF9_0080_precomp3d_v009.blend")  # different shot
        self.assertEqual(versioning.next_version(self.root, self.fields, "blend"), 2)

    def test_other_extensions_are_ignored(self):
        self.touch("0070_v007.nk")
        self.assertEqual(versioning.next_version(self.root, self.fields, "blend"), 1)

    def test_bump_derives_from_the_open_file(self):
        self.touch("0070_v001.blend")
        path, version = versioning.bump(self.root / "0070_v001.blend")
        self.assertEqual(version, 2)
        self.assertEqual(path.name, "0070_v002.blend")


class TestPaths(unittest.TestCase):
    def setUp(self):
        self.config = Config().with_roots(work_root="X:/work", render_root="Y:/render")

    def parts(self, path):
        return Path(path).as_posix()

    def test_work_file_layout(self):
        path = workfiles.work_file(sample_context(3), "blender", config=self.config)
        self.assertEqual(
            self.parts(path),
            "X:/work/FF9/0070/precomp3d/0070_v003.blend")

    def test_render_streams_share_one_version(self):
        context = sample_context(3)
        main = self.parts(workfiles.render_file(context, "main", config=self.config))
        offline = self.parts(workfiles.render_file(context, "offline", config=self.config))

        # Nothing in here repeats what the folders above already say, and
        # `main` has no folder of its own - a directory called main next to
        # nothing else says nothing.
        self.assertEqual(
            main,
            "Y:/render/FF9/0070/precomp3d/Render/v003/0070_v003.####.exr")
        # Same context, same version folder, different stream and format.
        self.assertIn("/offline/", offline)
        self.assertTrue(offline.endswith(".mov"))
        self.assertIn("v003", offline)

    def test_unknown_stream_is_refused(self):
        with self.assertRaises(ValueError):
            workfiles.render_dir(sample_context(), "nope", config=self.config)

    def test_blank_root_is_refused_rather_than_guessed(self):
        with self.assertRaises(workfiles.RootNotConfigured):
            workfiles.work_dir(sample_context(), Config())


class TestContext(unittest.TestCase):
    def test_survives_a_dict_round_trip(self):
        context = sample_context(5)
        restored = EntityContext.from_dict(context.to_dict())
        self.assertEqual(restored, context)

    def test_unknown_keys_are_tolerated(self):
        data = sample_context().to_dict()
        data["something_a_newer_build_added"] = True
        self.assertEqual(EntityContext.from_dict(data).entity, "0070")

    def test_from_filename_recovers_names_but_not_ids(self):
        recovered = EntityContext.from_filename("0070_v008.blend")
        self.assertEqual(recovered.entity, "0070")
        self.assertEqual(recovered.version, 8)
        self.assertEqual(recovered.task_id, "")

    def test_at_version_does_not_mutate_the_original(self):
        context = sample_context(3)
        other = context.at_version(9)
        self.assertEqual(context.version, 3)
        self.assertEqual(other.version, 9)
        self.assertEqual(other.task_id, context.task_id)


class TestConfig(unittest.TestCase):
    def test_override_merges_per_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / "config.toml"
            override.write_text('[naming]\nversion_padding = 4\n')

            from BB_core import config as config_module
            merged = Config(config_module.load(str(override), refresh=True))

            self.assertEqual(merged.naming["version_padding"], 4)
            # Untouched keys survive.
            self.assertEqual(merged.naming["base"],
                             "{entity}")
            self.assertEqual(
                naming.format_versioned(sample_context().as_fields(), 3, merged),
                "0070_v0003")


class TestPerProjectRoots(unittest.TestCase):
    """Roots and layout differ per show, so [projects.*] overrides them."""

    def config_with(self, table):
        from BB_core.config import Config as RawConfig
        data = RawConfig().data
        data["projects"] = table
        return RawConfig(data).with_roots(work_root="X:/work", render_root="Y:/render")

    def test_root_is_the_project_folder(self):
        # One show per root: the layout starts inside the project folder, so
        # the project name must not appear again underneath it.
        config = Config().with_roots(work_root="X:/PizzaHunt", render_root="Y:/render")
        path = workfiles.work_dir(sample_context(), config)
        self.assertEqual(Path(path).as_posix(), "X:/PizzaHunt/FF9/0070/precomp3d")

    def test_project_can_take_its_own_root(self):
        config = self.config_with({"VIL": {"work_root": "P:/villa/work"}})
        path = workfiles.work_dir(sample_context(), config)
        self.assertEqual(Path(path).as_posix(), "P:/villa/work/FF9/0070/precomp3d")

    def test_project_can_take_its_own_layout(self):
        # A studio sharing one root across shows puts {project} back in.
        config = self.config_with({"VIL": {
            "work_root": "P:/shows",
            "work_dir_shot": "{project}/{group}/{entity}/{task}"}})
        path = workfiles.work_dir(sample_context(), config)
        self.assertEqual(Path(path).as_posix(), "P:/shows/VIL/FF9/0070/precomp3d")

    def test_matching_ignores_case(self):
        # The name comes from Kitsu; a capital letter must not silently drop
        # the show back onto the default root.
        config = self.config_with({"vil": {"work_root": "P:/villa/work"}})
        self.assertTrue(str(workfiles.work_dir(sample_context(), config))
                        .startswith("P:"))

    def test_other_projects_are_untouched(self):
        config = self.config_with({"OTHER": {"work_root": "Z:/other"}})
        self.assertTrue(str(workfiles.work_dir(sample_context(), config))
                        .startswith("X:"))

    def test_render_streams_follow_the_project_root(self):
        config = self.config_with({"VIL": {"render_root": "P:/villa/render"}})
        path = workfiles.render_file(sample_context(3), "offline", config=config)
        self.assertTrue(Path(path).as_posix().startswith("P:/villa/render/FF9/"))


class TestHostNormalising(unittest.TestCase):
    """A LAN Kitsu is addressed by IP, and TLS on a bare IP never validates."""

    def host(self, given):
        from BB_core.kitsu import KitsuClient
        return KitsuClient(given).host

    def test_scheme_is_kept_when_given(self):
        self.assertEqual(self.host("http://192.168.50.121:8080"),
                         "http://192.168.50.121:8080")
        self.assertEqual(self.host("https://kitsu.example.com"),
                         "https://kitsu.example.com")

    def test_bare_ip_defaults_to_http(self):
        self.assertEqual(self.host("192.168.50.121:8080"),
                         "http://192.168.50.121:8080")
        self.assertEqual(self.host("192.168.50.121"), "http://192.168.50.121")
        self.assertEqual(self.host("localhost:8080"), "http://localhost:8080")

    def test_hostname_defaults_to_https(self):
        self.assertEqual(self.host("kitsu.example.com"), "https://kitsu.example.com")

    def test_trailing_slash_is_dropped(self):
        self.assertEqual(self.host("https://kitsu.example.com/"),
                         "https://kitsu.example.com")


class TestAssets(unittest.TestCase):
    """The asset tree runs through the same code as the shot tree."""

    def setUp(self):
        self.config = Config().with_roots(work_root="X:/PizzaHunt",
                                          render_root="X:/PizzaHunt")

    def test_asset_names_use_the_same_scheme(self):
        self.assertEqual(sample_asset(1).versioned(), "knife_v001")

    def test_assets_land_under_their_own_prefix(self):
        # A sequence called Prop must not collide with the asset type Prop.
        path = workfiles.work_file(sample_asset(1), "blender", config=self.config)
        self.assertEqual(
            Path(path).as_posix(),
            "X:/PizzaHunt/assets/Prop/knife/Modeling/knife_v001.blend")

    def test_shots_and_assets_do_not_collide(self):
        clash_shot = EntityContext(entity_type="shot", project="VIL", group="Prop",
                                   entity="knife", task="Modeling", version=1)
        shot_path = workfiles.work_dir(clash_shot, self.config)
        asset_path = workfiles.work_dir(sample_asset(1), self.config)
        self.assertNotEqual(str(shot_path), str(asset_path))

    def test_asset_renders_get_the_same_stream_layout(self):
        path = workfiles.render_file(sample_asset(2), "main", config=self.config)
        self.assertIn("/assets/Prop/knife/Modeling/Render/v002/",
                      Path(path).as_posix())
        self.assertTrue(Path(path).name.startswith("knife_v002."),
                        "the file names the asset, not the whole context: %s"
                        % Path(path).name)


class TestSchemaMigration(unittest.TestCase):
    """Stamps written before assets existed named the levels sequence/shot."""

    def test_old_stamp_is_translated(self):
        old = {"project": "VIL", "sequence": "FF9", "shot": "0070",
               "task": "precomp3d", "sequence_id": "s-1", "shot_id": "sh-1",
               "task_id": "t-1", "version": 3, "schema": 1}
        restored = EntityContext.from_dict(old)
        self.assertEqual(restored.group, "FF9")
        self.assertEqual(restored.entity, "0070")
        self.assertEqual(restored.entity_id, "sh-1")
        self.assertEqual(restored.entity_type, "shot")
        self.assertEqual(restored.versioned(), "0070_v003")


class TestPerDccTasks(unittest.TestCase):
    """Blender offers 3D tasks; Nuke offers compositing ones."""

    def test_blender_and_nuke_ask_for_different_departments(self):
        config = Config()
        blender = {d.lower() for d in config.dcc("blender")["departments"]}
        nuke = {d.lower() for d in config.dcc("nuke")["departments"]}

        self.assertIn("lighting", blender)
        self.assertIn("modeling", blender)
        self.assertNotIn("compositing", blender)
        self.assertIn("compositing", nuke)
        self.assertFalse(blender & nuke)

    def test_extensions_differ_per_dcc(self):
        config = Config()
        self.assertEqual(config.dcc("blender")["ext"], "blend")
        self.assertEqual(config.dcc("nuke")["ext"], "nk")


class TestFetchEnvelope(unittest.TestCase):
    """Kitsu entities carry their own `data` field; only pages get unwrapped."""

    def client(self, payload):
        from BB_core.kitsu import KitsuClient
        client = KitsuClient("http://kitsu.test")
        client._request = lambda method, path, **kwargs: payload
        return client

    def test_a_single_entity_is_returned_whole(self):
        # A task has a `data` attribute for custom fields. Unwrapping on that
        # key alone returned the custom data instead of the task, which broke
        # the status lookup the publish call depends on.
        task = {"id": "t1", "task_status_id": "st1", "data": None}
        self.assertEqual(self.client(task)._fetch("tasks/t1"), task)

    def test_entity_with_populated_custom_data_survives(self):
        task = {"id": "t1", "task_status_id": "st1", "data": {"note": "x"}}
        self.assertEqual(self.client(task)._fetch("tasks/t1")["task_status_id"], "st1")

    def test_a_paginated_page_is_unwrapped(self):
        page = {"data": [{"id": "a"}, {"id": "b"}], "nb_pages": 1, "page": 1}
        self.assertEqual(len(self.client(page)._fetch("shots")), 2)

    def test_a_plain_list_passes_through(self):
        rows = [{"id": "a"}]
        self.assertEqual(self.client(rows)._fetch("shots"), rows)

    def test_nothing_becomes_an_empty_list(self):
        self.assertEqual(self.client(None)._fetch("shots"), [])


class TestFrameRange(unittest.TestCase):
    """Kitsu is loose about where a frame range lives and what type it is."""

    def test_the_real_shape_from_the_studio_server(self):
        shot = {"nb_frames": 252, "frame_in": None, "frame_out": None,
                "data": {"frame_in": 1001, "frame_out": 1252}}
        self.assertEqual(frames.frame_range(shot), (1001, 1252))
        self.assertEqual(frames.frame_count(shot), 252)

    def test_kitsu_columns_win_over_custom_data(self):
        shot = {"frame_in": 10, "frame_out": 20, "data": {"frame_in": 999}}
        self.assertEqual(frames.frame_range(shot), (10, 20))

    def test_strings_are_accepted(self):
        shot = {"data": {"frame_in": "1001", "frame_out": "1100"}}
        self.assertEqual(frames.frame_range(shot), (1001, 1100))

    def test_reversed_values_are_ordered(self):
        shot = {"data": {"frame_in": 1100, "frame_out": 1001}}
        self.assertEqual(frames.frame_range(shot), (1001, 1100))

    def test_length_alone_still_gives_a_range(self):
        self.assertEqual(frames.frame_range({"nb_frames": 100}), (1, 100))

    def test_a_start_plus_a_length(self):
        shot = {"nb_frames": 100, "data": {"frame_in": 1001}}
        self.assertEqual(frames.frame_range(shot), (1001, 1100))

    def test_nothing_known_is_none(self):
        self.assertIsNone(frames.frame_range({}))
        self.assertIsNone(frames.frame_range({"nb_frames": 0}))
        self.assertIsNone(frames.frame_range(None))

    def test_junk_does_not_raise(self):
        self.assertIsNone(frames.frame_range({"data": {"frame_in": "soon"}}))


class TestFps(unittest.TestCase):
    def test_project_fps_arrives_as_a_string(self):
        self.assertEqual(frames.fps({"fps": "24"}), 24.0)

    def test_a_shot_overrides_its_project(self):
        self.assertEqual(frames.fps({"fps": "24"}, {"data": {"fps": "25"}}), 25.0)

    def test_missing_is_none(self):
        self.assertIsNone(frames.fps({}))
        self.assertIsNone(frames.fps({"fps": ""}))
        self.assertIsNone(frames.fps({"fps": "not a number"}))

    def test_whole_rates(self):
        self.assertEqual(frames.fps_to_rational(24), (24, 1.0))
        self.assertEqual(frames.fps_to_rational(25), (25, 1.0))

    def test_broadcast_rates_keep_their_pulldown(self):
        # Rounding 23.976 to 24 drifts a frame every forty seconds, which only
        # shows up once a cut is conformed.
        self.assertEqual(frames.fps_to_rational(23.976), (24, 1.001))
        self.assertEqual(frames.fps_to_rational(29.97), (30, 1.001))
        self.assertEqual(frames.fps_to_rational(59.94), (60, 1.001))

    def test_describe_reads_the_way_people_say_it(self):
        self.assertEqual(frames.describe(24.0), "24 fps")
        self.assertEqual(frames.describe(23.976), "23.976 fps")
        self.assertEqual(frames.describe(None), "unknown")

    def test_nothing_is_none(self):
        self.assertIsNone(frames.fps_to_rational(0))
        self.assertIsNone(frames.fps_to_rational(None))


# The tree actually set on the studio's PizzaHunt project.
KITSU_TREE = {
    "working": {
        "mountpoint": "", "root": "",
        "folder_path": {"shot": "<Sequence>/<Shot>/<TaskType>",
                        "asset": "assets/<AssetType>/<Asset>/<TaskType>"},
        # Mirrors the local default, which is the whole point of the tests
        # below: adopting the tree must not move a single file.
        "file_name": {"shot": "<Shot>", "asset": "<Asset>"},
    },
}


class TestKitsuFileTree(unittest.TestCase):
    """A layout defined on the Kitsu project outranks the local config."""

    def config_with(self, tree):
        return Config().for_kitsu_project({"file_tree": tree}).with_roots(
            work_root="X:/PizzaHunt", render_root="X:/PizzaHunt")

    def test_no_tree_changes_nothing(self):
        plain = Config()
        self.assertIs(plain.for_kitsu_project({}), plain)
        self.assertIs(plain.for_kitsu_project({"file_tree": None}), plain)
        self.assertIs(plain.for_kitsu_project(None), plain)

    def test_the_studio_tree_translates(self):
        self.assertEqual(filetree.translate({"file_tree": KITSU_TREE}), {
            "paths": {"work_dir_shot": "{group}/{entity}/{task}",
                      "work_dir_asset": "assets/{group}/{entity}/{task}"},
            "naming": {"base": "{entity}"},
        })

    def test_parallel_shot_and_asset_names_collapse_to_one(self):
        # <Sequence>/<Shot> and <AssetType>/<Asset> both become {group}/{entity},
        # so there is no reason to carry two identical templates.
        translated = filetree.translate({"file_tree": KITSU_TREE})["naming"]
        self.assertIn("base", translated)
        self.assertNotIn("base_shot", translated)

    def test_genuinely_different_names_stay_apart(self):
        tree = {"working": {"file_name": {
            "shot": "<Project>_<Sequence>_<Shot>_<TaskType>",
            "asset": "<Asset>_<TaskType>"}}}
        naming_part = filetree.translate({"file_tree": tree})["naming"]
        self.assertEqual(naming_part["base_asset"], "{entity}_{task}")
        self.assertEqual(naming_part["base_shot"],
                         "{project}_{group}_{entity}_{task}")

    def test_paths_match_what_the_local_config_produced(self):
        # The tree was written to mirror the existing layout, so adopting it
        # must not move a single file.
        kitsu = self.config_with(KITSU_TREE)
        local = Config().with_roots(work_root="X:/PizzaHunt",
                                    render_root="X:/PizzaHunt")
        for context in (sample_context(3), sample_asset(1)):
            self.assertEqual(
                workfiles.work_file(context, "blender", config=kitsu),
                workfiles.work_file(context, "blender", config=local))

    def test_uppercase_style_is_honoured(self):
        tree = {"working": {"file_name": {
            "shot": "<Project>_<Sequence>_<Shot>_<TaskType>",
            "asset": "<Project>_<AssetType>_<Asset>_<TaskType>",
            "style": "uppercase"}}}
        config = self.config_with(tree)
        self.assertEqual(sample_context(3).versioned(config=config),
                         "VIL_FF9_0070_PRECOMP3D_v003")

    def test_case_is_left_alone_by_default(self):
        # Zou lowercases when no style is given. Names are left as production
        # spells them instead, which is why this is a deliberate divergence.
        config = self.config_with(KITSU_TREE)
        self.assertEqual(sample_context(3).versioned(config=config),
                         "0070_v003")

    def test_a_token_with_no_field_is_refused_not_fudged(self):
        tree = {"working": {"folder_path": {"shot": "<Episode>/<Sequence>/<Shot>"}}}
        with self.assertRaises(filetree.UnsupportedTree):
            filetree.translate({"file_tree": tree})

    def test_an_unusable_tree_falls_back_instead_of_breaking(self):
        tree = {"working": {"folder_path": {"shot": "<Episode>/<Shot>"}}}
        plain = Config()
        self.assertIs(plain.for_kitsu_project({"file_tree": tree}), plain)
        self.assertIn("ignored", filetree.describe({"file_tree": tree}))

    def test_names_round_trip_through_a_split_config(self):
        tree = {"working": {"file_name": {
            "shot": "<Project>_<Sequence>_<Shot>_<TaskType>",
            "asset": "<Asset>_<TaskType>"}}}
        config = self.config_with(tree)

        asset_name = sample_asset(2).versioned(config=config)
        self.assertEqual(asset_name, "knife_Modeling_v002")
        parsed = naming.parse(asset_name, config)
        self.assertEqual(parsed["entity"], "knife")
        self.assertEqual(parsed["version"], 2)


# The brief actually written on the studio's PizzaHunt project.
KITSU_BRIEF = """Pizza Hunt - test project for the BB Kitsu pipeline.

Anything written here is just notes, except the block below, which the
Blender and Nuke tools read as settings.

[bb]
work_root = "I:/PizzaHunt"
render_root = "I:/PizzaHunt"
"""


class TestBrief(unittest.TestCase):
    """Settings a producer can type into Kitsu's Brief box."""

    def test_the_studio_brief_parses(self):
        self.assertEqual(brief.parse(KITSU_BRIEF), {
            "paths": {"work_root": "I:/PizzaHunt", "render_root": "I:/PizzaHunt"}})

    def test_prose_without_a_block_is_just_prose(self):
        self.assertIsNone(brief.parse("Delivery 12 December. No block here."))
        self.assertIsNone(brief.parse(""))
        self.assertIsNone(brief.parse(None))

    def test_bare_keys_are_paths(self):
        parsed = brief.parse('[bb]\nwork_root = "X:/show"')
        self.assertEqual(parsed["paths"]["work_root"], "X:/show")

    def test_sub_tables_reach_the_other_sections(self):
        parsed = brief.parse(
            '[bb]\nwork_root = "X:/s"\n[bb.naming]\nversion_padding = 4')
        self.assertEqual(parsed["naming"]["version_padding"], 4)
        self.assertEqual(parsed["paths"]["work_root"], "X:/s")

    def test_an_unknown_section_is_ignored_not_invented(self):
        parsed = brief.parse(
            '[bb]\nwork_root = "X:/s"\n[bb.nonsense]\nthing = 1')
        self.assertNotIn("nonsense", parsed)

    def test_prose_after_the_block_is_not_swallowed(self):
        text = ('[bb]\nwork_root = "X:/s"\n\n'
                '[Notes]\nthis is not toml at all: << >>')
        self.assertEqual(brief.parse(text)["paths"]["work_root"], "X:/s")

    def test_markup_is_stripped_in_case_the_brief_goes_rich_text(self):
        text = '<p>Notes</p><pre>[bb]\nwork_root = &quot;X:/s&quot;</pre>'
        self.assertEqual(brief.parse(text)["paths"]["work_root"], "X:/s")

    def test_a_broken_block_is_reported_not_ignored(self):
        # Silently dropping a typo would write files somewhere unexpected.
        with self.assertRaises(brief.BadBrief):
            brief.parse('[bb]\nwork_root = "unclosed')
        self.assertIn("not valid TOML",
                      brief.describe({"description": '[bb]\nwork_root = "oops'}))

    def test_the_brief_supplies_roots_the_preferences_never_had(self):
        config = Config().for_kitsu_project(
            {"description": KITSU_BRIEF, "file_tree": KITSU_TREE})
        self.assertEqual(config.paths["work_root"], "I:/PizzaHunt")
        path = workfiles.work_file(sample_context(1), "blender", config=config)
        self.assertEqual(
            Path(path).as_posix(),
            "I:/PizzaHunt/FF9/0070/precomp3d/0070_v001.blend")

    def test_the_brief_outranks_the_tree(self):
        # The brief is the more deliberate of the two, so it goes last.
        tree = {"working": {"folder_path": {"shot": "<Sequence>/<Shot>"}}}
        config = Config().for_kitsu_project({
            "file_tree": tree,
            "description": ('[bb]\n[bb.paths]\n'
                            'work_dir_shot = "{group}/{entity}/{task}"'),
        })
        self.assertEqual(config.paths["work_dir_shot"], "{group}/{entity}/{task}")

    def test_a_project_saying_nothing_changes_nothing(self):
        plain = Config()
        self.assertIs(plain.for_kitsu_project({"description": "just notes"}), plain)


class TestNormalize(unittest.TestCase):
    """Zou conforms movie uploads to the project resolution unless told not to."""

    def upload(self, **kwargs):
        from BB_core.kitsu import KitsuClient
        from BB_core.transport import Response

        client = KitsuClient("http://kitsu.test")
        posted = []

        class Recording:
            name = "recording"

            @staticmethod
            def request(method, url, **arguments):
                posted.append(url)
                if url.endswith("/comment"):
                    return Response(200, b'{"id": "c1"}')
                return Response(200, b'{"id": "p1"}')

        client.transport = Recording()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.mp4"
            path.write_bytes(b"x")
            client.publish_preview("t1", str(path), task_status_id="st1",
                                   set_main=False, **kwargs)

        return [u for u in posted if "pictures/preview-files" in u][0]

    def test_normalising_is_off_by_default(self):
        # On, Zou upscales a quarter-resolution test render to full size and
        # re-encodes what was already H.264.
        self.assertIn("normalize=false", self.upload())

    def test_it_can_be_asked_for(self):
        self.assertNotIn("normalize", self.upload(normalize=True))


class TestTransport(unittest.TestCase):
    """The core must work in a Python with no third-party packages at all.

    Nuke 16 ships urllib, ssl and certifi and no requests, so the urllib
    backend is not a fallback nobody exercises - it is the one Nuke uses.
    """

    def test_urllib_is_always_available(self):
        from BB_core import transport
        self.assertIn("urllib", transport.available())

    def test_a_backend_can_be_forced(self):
        from BB_core import transport
        self.assertEqual(transport.make_transport(prefer="urllib").name, "urllib")

    def test_query_params_are_appended(self):
        from BB_core.transport import with_params
        self.assertEqual(with_params("http://x/y", {"a": "1"}), "http://x/y?a=1")
        self.assertEqual(with_params("http://x/y?b=2", {"a": "1"}),
                         "http://x/y?b=2&a=1")
        self.assertEqual(with_params("http://x/y", None), "http://x/y")
        self.assertEqual(with_params("http://x/y", {"a": None}), "http://x/y")

    def test_multipart_streams_rather_than_buffering(self):
        from BB_core.transport import _multipart

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp4"
            path.write_bytes(b"0123456789")
            stream, content_type, length = _multipart(str(path))

            self.assertIn("multipart/form-data; boundary=", content_type)
            body = stream.read()
            self.assertEqual(len(body), length)
            self.assertIn(b'filename="clip.mp4"', body)
            self.assertIn(b"0123456789", body)
            self.assertTrue(body.rstrip().endswith(b"--"))

    def test_multipart_reads_in_pieces(self):
        # read(n) has to honour n, or urllib cannot stream a large upload.
        from BB_core.transport import _multipart

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp4"
            path.write_bytes(b"abcdefghij")
            stream, _type, length = _multipart(str(path))

            collected = b""
            while True:
                chunk = stream.read(7)
                if not chunk:
                    break
                self.assertLessEqual(len(chunk), 7)
                collected += chunk
            self.assertEqual(len(collected), length)


class TestExplain(unittest.TestCase):
    """A wrong address must not read like a wrong password."""

    def message(self, error, server="http://kitsu.test:8080",
                email="artist@studio.com"):
        from BB_core.kitsu import explain
        return explain(error, server, email)

    def test_unreachable_says_so(self):
        from BB_core.kitsu import KitsuError

        # The real text carries the whole urllib3 stack; none of it belongs
        # in a dialog, and it reads like a rejection when it is not one.
        raw = KitsuError("cannot reach http://192.165.50.121:8080: "
                         "HTTPConnectionPool(host='192.165.50.121', port=8080): "
                         "Max retries exceeded")
        message = self.message(raw)
        self.assertIn("Cannot reach", message)
        self.assertIn("check the address", message)
        self.assertNotIn("HTTPConnectionPool", message)
        self.assertNotIn("password", message)

    def test_a_rejected_login_names_both_fields(self):
        from BB_core.kitsu import AuthError

        message = self.message(AuthError("Kitsu rejected the login for x"))
        # Both fields, because Kitsu answers identically for an unknown email
        # and a wrong password - and the email is echoed so a typo in it is
        # visible rather than invisible.
        self.assertIn("check the email and the password", message)
        self.assertIn("artist@studio.com", message)

    def test_two_factor_keeps_its_own_wording(self):
        from BB_core.kitsu import AuthError

        message = self.message(AuthError("this account needs two-factor auth, "
                                         "which is not supported yet"))
        self.assertIn("two-factor", message)

    def test_anything_else_passes_through(self):
        from BB_core.kitsu import KitsuError
        self.assertIn("HTTP 500", self.message(KitsuError("data/x failed: HTTP 500")))


class TestRenderLayoutSaysEachThingOnce(unittest.TestCase):
    """Nothing in a render path repeats what the path already said.

    The first layout spelled the whole context into the version folder and
    again into the filename, under an `internalRender/main` nobody had asked
    for, giving paths like

        .../assets/Prop/Kitchen-counter/Modeling/internalRender/main/
        Misery-Loves-Company_Prop_Kitchen-counter_Modeling_v002/
        Misery-Loves-Company_Prop_Kitchen-counter_Modeling_v002.0001.exr

    where the project is named three times and the asset twice.
    """

    def setUp(self):
        self.config = Config()
        self.config.paths["render_root"] = "E:/Misery"
        self.asset = EntityContext(
            project="Misery Loves Company", group="Prop",
            entity="Kitchen-counter", task="Modeling",
            entity_type="asset", version=2)
        self.shot = EntityContext(
            project="Misery Loves Company", group="sc01", entity="sh03",
            task="Lighting", entity_type="shot", version=12)

    def posix(self, path):
        return Path(path).as_posix()

    def test_the_project_is_not_named_below_its_own_root(self):
        path = self.posix(workfiles.render_file(self.asset, "main",
                                                config=self.config))
        below = path.split("E:/Misery/", 1)[1]
        self.assertNotIn("Misery", below, path)

    def test_the_entity_is_named_once_in_the_file_and_not_in_the_folder(self):
        path = Path(workfiles.render_file(self.asset, "main", config=self.config))
        self.assertEqual(path.name, "Kitchen-counter_v002.####.exr")
        self.assertEqual(path.parent.name, "v002")

    def test_the_task_is_a_folder_not_part_of_the_name(self):
        # Dropped from the filename, kept as a folder: a prop can be rendered
        # from modelling and from lighting, and those must not collide.
        path = Path(workfiles.render_file(self.asset, "main", config=self.config))
        self.assertIn("Modeling", path.parts)
        self.assertNotIn("Modeling", path.name)

    def test_main_has_no_folder_of_its_own(self):
        path = self.posix(workfiles.render_dir(self.shot, "main", self.config))
        self.assertNotIn("/main", path)
        self.assertTrue(path.endswith("/Render/v012"), path)

    def test_another_stream_still_gets_one(self):
        path = self.posix(workfiles.render_dir(self.shot, "proxy", self.config))
        self.assertTrue(path.endswith("/Render/proxy/v012"), path)

    def test_the_output_prefix_matches_what_is_rendered(self):
        prefix = str(workfiles.render_output(self.shot, "main", self.config))
        rendered = str(workfiles.render_file(self.shot, "main", "0001",
                                             self.config))
        self.assertTrue(rendered.startswith(prefix),
                        "%s does not start with %s" % (rendered, prefix))


class TestRenderVersionsReadsBothLayouts(unittest.TestCase):
    """Renders made before the layout changed still have to be listed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config = Config()
        self.config.paths["render_root"] = self._tmp.name
        self.context = EntityContext(
            project="PizzaHunt", group="sc01", entity="sh01", task="Lighting",
            entity_type="shot", version=1)

    def tearDown(self):
        self._tmp.cleanup()

    def stream_folder(self):
        return workfiles.render_dir(self.context.at_version(1), "main",
                                    self.config).parent

    def test_the_current_layout_is_read(self):
        folder = self.stream_folder() / "v004"
        folder.mkdir(parents=True)
        (folder / "sh01_v004.1001.exr").write_text("")
        found = workfiles.render_versions(self.context, "main", self.config)
        self.assertEqual([row[0] for row in found], [4])

    def test_the_older_spelled_out_layout_is_still_read(self):
        folder = self.stream_folder() / "PizzaHunt_sc01_sh01_Lighting_v002"
        folder.mkdir(parents=True)
        (folder / "PizzaHunt_sc01_sh01_Lighting_v002.1001.exr").write_text("")
        found = workfiles.render_versions(self.context, "main", self.config)
        self.assertEqual([row[0] for row in found], [2])

    def test_a_folder_that_names_neither_is_ignored(self):
        folder = self.stream_folder() / "scratch"
        folder.mkdir(parents=True)
        (folder / "whatever.1001.exr").write_text("")
        self.assertEqual(workfiles.render_versions(self.context, "main",
                                                   self.config), [])


class TestVersionThumbnails(unittest.TestCase):
    """The picture the browser shows beside each version.

    Kitsu cannot answer this. Its preview files are numbered by a revision
    counter that counts publishes and review comments, so revision 3 is
    neither version 3 nor reliably tied to any version - measured against a
    real project. The picture therefore has to be written on save.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config = Config()
        self.config.paths["work_root"] = str(self.root)
        self.context = sample_context()

    def tearDown(self):
        self._tmp.cleanup()

    def source(self, name="grab.png"):
        path = self.root / name
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return path

    def test_thumb_sits_beside_the_work_file(self):
        work = workfiles.work_file(self.context, "blender", 7, self.config)
        thumb = workfiles.thumb_file(self.context, "blender", 7, self.config)
        self.assertEqual(thumb.parent.parent, work.parent)
        self.assertEqual(thumb.parent.name, workfiles.THUMB_DIR)

    def test_one_thumb_per_version(self):
        first = workfiles.thumb_file(self.context, "blender", 1, self.config)
        second = workfiles.thumb_file(self.context, "blender", 2, self.config)
        self.assertNotEqual(first.name, second.name)
        self.assertTrue(first.name.endswith(".png"))

    def test_the_dcc_does_not_change_the_name(self):
        # One picture per version, whichever application saved it - the
        # browser looks the version up, not the application.
        blender = workfiles.thumb_file(self.context, "blender", 3, self.config)
        nuke = workfiles.thumb_file(self.context, "nuke", 3, self.config)
        self.assertEqual(blender, nuke)

    def test_saving_copies_the_picture_into_place(self):
        stored = workfiles.save_thumb(self.context, "blender", self.source(),
                                      4, self.config)
        self.assertIsNotNone(stored)
        self.assertTrue(stored.is_file())
        self.assertEqual(stored,
                         workfiles.thumb_file(self.context, "blender", 4,
                                              self.config))

    def test_saving_makes_the_folder(self):
        target = workfiles.thumb_file(self.context, "blender", 5, self.config)
        self.assertFalse(target.parent.exists())
        workfiles.save_thumb(self.context, "blender", self.source(), 5,
                             self.config)
        self.assertTrue(target.is_file())

    def test_a_missing_source_is_not_an_error(self):
        # A save must never fail because the thumbnail could not be grabbed.
        self.assertIsNone(
            workfiles.save_thumb(self.context, "blender",
                                 self.root / "nothing.png", 6, self.config))

    def test_no_source_is_not_an_error(self):
        self.assertIsNone(
            workfiles.save_thumb(self.context, "blender", None, 6, self.config))

    def test_resaving_replaces_the_picture(self):
        workfiles.save_thumb(self.context, "blender", self.source("a.png"),
                             8, self.config)
        newer = self.root / "b.png"
        newer.write_bytes(b"\x89PNG\r\n\x1a\nNEWER")
        stored = workfiles.save_thumb(self.context, "blender", newer, 8,
                                      self.config)
        self.assertTrue(stored.read_bytes().endswith(b"NEWER"))


class TestVersionsAgree(unittest.TestCase):
    """One version, declared in three files that cannot import each other.

    The Blender manifest is TOML read by Blender itself, the Nuke package is
    plain Python, and BB_core is shared by both - so nothing enforces that
    they match except this. They sat at 0.1.0 through every change until
    somebody asked which build was actually loaded.
    """

    def declared(self, path, pattern):
        text = (REPO / path).read_text(encoding="utf-8")
        found = re.search(pattern, text, re.M)
        self.assertIsNotNone(found, "no version found in %s" % path)
        return found.group(1)

    def test_all_three_agree(self):
        core = self.declared("BB_core/__init__.py",
                             r'^__version__ = "([^"]+)"')
        manifest = self.declared("blender/BB_pipeline/blender_manifest.toml",
                                 r'^version = "([^"]+)"')
        nuke = self.declared("nuke/BB_pipeline_nuke/__init__.py",
                             r"^__version__ = '([^']+)'")
        self.assertEqual(core, manifest,
                         "BB_core and the Blender manifest disagree")
        self.assertEqual(core, nuke, "BB_core and the Nuke package disagree")


class TestSiblingModuleNames(unittest.TestCase):
    """Names that only blow up when a draw callback runs.

    Blender swallows an exception in draw and simply stops drawing, so a
    module referenced but never imported, or an attribute that lives on the
    Nuke session rather than the Blender one, shows up as a browser that
    renders half of itself. Both have happened. Neither is reachable by the
    tests that matter, so they are caught statically instead.
    """

    PACKAGES = ("blender/BB_pipeline", "nuke/BB_pipeline_nuke")

    def modules_in(self, package):
        return {path.stem: path for path in (REPO / package).glob("*.py")
                if path.stem != "__init__"}

    def top_level_names(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                found.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        found.add(target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    found.add(alias.asname or alias.name.split(".")[0])
        return found

    def imported_siblings(self, path, siblings):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                for alias in node.names:
                    name = alias.asname or alias.name
                    if name in siblings:
                        found.add(name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = (alias.asname or alias.name).split(".")[-1]
                    if name in siblings:
                        found.add(name)
        return found

    # A scope-aware check for "module used but never imported" was tried and
    # removed: several modules import their siblings inside the function that
    # needs them, and telling that apart from a genuine miss needs a real
    # scope walker. pyflakes does this properly if it is ever worth wiring in.
    # What stays is the half that is exact - an attribute that the sibling
    # does not define, which is how session.config_for reached Blender.

    def test_sibling_attributes_exist(self):
        problems = []
        for package in self.PACKAGES:
            modules = self.modules_in(package)
            defined = {n: self.top_level_names(p) for n, p in modules.items()}
            for name, path in sorted(modules.items()):
                imported = self.imported_siblings(path, set(modules))
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Attribute)
                            and isinstance(node.value, ast.Name)
                            and node.value.id in imported
                            and not node.attr.startswith("__")
                            and node.attr not in defined[node.value.id]):
                        problems.append(
                            "%s/%s.py calls %s.%s which %s does not define"
                            % (package, name, node.value.id, node.attr,
                               node.value.id))
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


class TestSelfAttributesAreAssigned(unittest.TestCase):
    """Attributes read off self that nothing in the class ever sets.

    Removing the middle column deleted the sequence, shot and task combo
    boxes but left context() reading them. Python only complains when the
    line runs, and it ran inside the version refresh - so the browser showed
    an empty list instead of an error, and looked like a thumbnail problem.
    """

    FILES = ("nuke/BB_pipeline_nuke/browser.py",
             "blender/BB_pipeline/operators.py",
             "blender/BB_pipeline/treeview.py")

    def assigned_in(self, node):
        """Every ``self.x`` the class body ever writes to."""
        found = set()
        for sub in ast.walk(node):
            targets = []
            if isinstance(sub, ast.Assign):
                targets = sub.targets
            elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)):
                targets = [sub.target]
            elif isinstance(sub, (ast.For, ast.AsyncFor)):
                targets = [sub.target]
            for target in targets:
                for part in ast.walk(target):
                    if (isinstance(part, ast.Attribute)
                            and isinstance(part.value, ast.Name)
                            and part.value.id == "self"):
                        found.add(part.attr)
            # setattr(self, 'name', ...) counts as an assignment.
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "setattr"
                    and sub.args
                    and isinstance(sub.args[0], ast.Name)
                    and sub.args[0].id == "self"):
                if len(sub.args) > 1 and isinstance(sub.args[1], ast.Constant):
                    found.add(sub.args[1].value)
        return found

    def defined_on(self, node):
        """Methods, class attributes and anything inherited by name."""
        found = set()
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.add(item.name)
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        found.add(target.id)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                found.add(item.target.id)
        return found

    def test_no_attribute_is_read_without_being_set(self):
        problems = []
        for relative in self.FILES:
            path = REPO / relative
            tree = ast.parse(path.read_text(encoding="utf-8"))
            # A mixin is completed by whatever it is combined with, so the
            # names it reads are not its own to declare.
            mixins = {b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "")
                      for node in ast.walk(tree)
                      if isinstance(node, ast.ClassDef) for b in node.bases}
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef) or node.name in mixins:
                    continue
                # Only classes that own their state. A Blender Operator gets
                # its properties from annotations and the RNA system, and a
                # Panel gets layout and friends from Blender itself.
                bases = {b.attr if isinstance(b, ast.Attribute)
                         else getattr(b, "id", "") for b in node.bases}
                if bases - {"object"}:
                    continue
                known = self.assigned_in(node) | self.defined_on(node)
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Attribute)
                            and isinstance(sub.value, ast.Name)
                            and sub.value.id == "self"
                            and isinstance(sub.ctx, ast.Load)
                            and not sub.attr.startswith("__")
                            and sub.attr not in known):
                        problems.append(
                            "%s:%d reads self.%s in %s, which nothing assigns"
                            % (relative, sub.lineno, sub.attr, node.name))
        self.assertEqual(problems, [], "\n" + "\n".join(sorted(set(problems))))


class TestRenderVersions(unittest.TestCase):
    """Rendered sequences on disk, for a task this application cannot author.

    A comper opening a lighting task has nothing to open - there is no Nuke
    script for it - but there is a sequence to read. Kitsu cannot supply it:
    what Kitsu holds is the review movie, re-encoded to H.264, which is not
    what anybody comps against.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config = Config()
        self.config.paths["render_root"] = str(self.root)
        self.config.paths["work_root"] = str(self.root)
        self.context = EntityContext(
            project="PizzaHunt", group="sc01", entity="sh01", task="Lighting",
            entity_type="shot", version=1)

    def tearDown(self):
        self._tmp.cleanup()

    def render(self, version, frames, task=None, ext="exr"):
        context = self.context if task is None else EntityContext(
            project="PizzaHunt", group="sc01", entity="sh01", task=task,
            entity_type="shot", version=version)
        folder = workfiles.render_dir(context.at_version(version), "main",
                                      self.config)
        folder.mkdir(parents=True, exist_ok=True)
        stem = context.versioned(version, self.config)
        for frame in frames:
            (folder / ("%s.%04d.%s" % (stem, frame, ext))).write_text("")
        return folder

    def test_nothing_rendered_is_empty(self):
        self.assertEqual(workfiles.render_versions(self.context, "main",
                                                   self.config), [])

    def test_a_rendered_version_is_found_with_its_range(self):
        self.render(1, range(1001, 1005))
        found = workfiles.render_versions(self.context, "main", self.config)
        self.assertEqual(len(found), 1)
        version, pattern, first, last = found[0]
        self.assertEqual((version, first, last), (1, 1001, 1004))

    def test_the_pattern_carries_a_placeholder_not_a_frame(self):
        # A Read node handed one concrete frame is a one-frame Read that
        # silently ignores the rest of the sequence.
        self.render(2, range(1001, 1003))
        _v, pattern, _f, _l = workfiles.render_versions(
            self.context, "main", self.config)[0]
        self.assertIn("%04d", pattern)
        self.assertNotIn("1001", Path(pattern).name)

    def test_versions_come_back_in_order_with_gaps_kept(self):
        self.render(3, range(1001, 1003))
        self.render(1, range(1001, 1003))
        found = workfiles.render_versions(self.context, "main", self.config)
        self.assertEqual([row[0] for row in found], [1, 3])

    def test_another_task_is_not_listed(self):
        # The render root holds every department's output side by side.
        self.render(1, range(1001, 1003))
        self.render(9, range(1001, 1003), task="Compositing")
        found = workfiles.render_versions(self.context, "main", self.config)
        self.assertEqual([row[0] for row in found], [1])

    def test_an_empty_version_folder_is_skipped(self):
        folder = workfiles.render_dir(self.context.at_version(4), "main",
                                      self.config)
        folder.mkdir(parents=True, exist_ok=True)
        self.assertEqual(workfiles.render_versions(self.context, "main",
                                                   self.config), [])

    def test_an_unknown_stream_is_not_an_error(self):
        self.assertEqual(workfiles.render_versions(self.context, "nosuch",
                                                   self.config), [])


class TestBlenderExtensionPolicy(unittest.TestCase):
    """What Blender's extension validator objects to, checked here instead.

    An add-on that writes to sys.path, or that imports a bundled package as a
    top-level module, is listed in the preferences under Warning - one line
    per module, which reads like something is broken. Both were true of the
    old bootstrap; the core is bound as BB_pipeline.BB_core now.

    Nuke has no such policy and keeps its plain `from BB_core import ...`,
    so this applies to the Blender add-on alone.
    """

    ADDON = "blender/BB_pipeline"

    def sources(self):
        return sorted((REPO / self.ADDON).glob("*.py"))

    def test_nothing_imports_bb_core_as_a_top_level_module(self):
        offenders = []
        for path in self.sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    # level 0 is an absolute import; anything higher is
                    # relative and therefore fine.
                    if not node.level and (node.module or "").split(".")[0] == "BB_core":
                        offenders.append("%s:%d from %s import ..."
                                         % (path.name, node.lineno, node.module))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] == "BB_core":
                            offenders.append("%s:%d import %s"
                                             % (path.name, node.lineno, alias.name))
        self.assertEqual(offenders, [], "\n" + "\n".join(offenders))

    def test_nothing_writes_to_sys_path(self):
        offenders = []
        for path in self.sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                target = node.func
                if not isinstance(target, ast.Attribute):
                    continue
                if target.attr not in ("insert", "append", "extend"):
                    continue
                owner = target.value
                if (isinstance(owner, ast.Attribute) and owner.attr == "path"
                        and isinstance(owner.value, ast.Name)
                        and owner.value.id == "sys"):
                    offenders.append("%s:%d sys.path.%s(...)"
                                     % (path.name, node.lineno, target.attr))
        self.assertEqual(offenders, [], "\n" + "\n".join(offenders))


class TestWindowsPathsInABrief(unittest.TestCase):
    r"""A root pasted into the Kitsu brief the way Windows writes it.

    TOML reads a backslash in a double-quoted string as an escape, so
    ``work_root = "E:\Misery Loves Company"`` is not a path with a typo - it
    is a parse error at ``\M``, and the whole block is discarded. What the
    artist then sees is "Set a Work Root" on a project that plainly has one.
    """

    REAL = ('Blender and Nuke tools read as settings.\n\n'
            '[bb]\n'
            'work_root = "E:\\Misery Loves Company"\n'
            'render_root = "E:\\Misery Loves Company"\n')

    def test_a_pasted_windows_path_parses(self):
        parsed = brief.parse(self.REAL)
        self.assertEqual(parsed["paths"]["work_root"],
                         "E:\\Misery Loves Company")
        self.assertEqual(parsed["paths"]["render_root"],
                         "E:\\Misery Loves Company")

    def test_a_trailing_backslash_survives(self):
        parsed = brief.parse('[bb]\nwork_root = "E:\\Show\\"\n')
        self.assertEqual(parsed["paths"]["work_root"], "E:\\Show\\")

    def test_forward_slashes_still_work(self):
        parsed = brief.parse('[bb]\nwork_root = "E:/Misery Loves Company"\n')
        self.assertEqual(parsed["paths"]["work_root"],
                         "E:/Misery Loves Company")

    def test_a_correctly_escaped_brief_is_unchanged(self):
        # Somebody who already knows TOML must not have their path doubled.
        parsed = brief.parse('[bb]\nwork_root = "E:\\\\Show"\n')
        self.assertEqual(parsed["paths"]["work_root"], "E:\\Show")

    def test_a_real_escape_is_left_alone(self):
        parsed = brief.parse('[bb]\nnote = "one\\ttwo"\n')
        self.assertEqual(parsed["paths"]["note"], "one\ttwo")

    def test_a_comment_after_the_value_survives(self):
        parsed = brief.parse('[bb]\nwork_root = "E:\\Show"  # the drive\n')
        self.assertEqual(parsed["paths"]["work_root"], "E:\\Show")

    def test_a_genuinely_broken_block_still_raises(self):
        # Leniency about backslashes must not turn into leniency about
        # everything - a typo that changes where files land is worth a noise.
        with self.assertRaises(brief.BadBrief):
            brief.parse('[bb]\nwork_root = "unterminated\n')

    def test_the_reason_is_reportable(self):
        project = {"description": '[bb]\nwork_root = "unterminated\n'}
        self.assertIn("will not parse", brief.problem(project))

    def test_nothing_to_report_when_it_parses(self):
        self.assertEqual(brief.problem({"description": self.REAL}), "")

    def test_nothing_to_report_without_a_block(self):
        self.assertEqual(brief.problem({"description": "just notes"}), "")


class TestVolumeTranslation(unittest.TestCase):
    r"""One root in Kitsu, two platforms reading it.

    A brief written on Windows says ``E:\Misery Loves Company``. On macOS the
    same disk is under /Volumes with whatever name it was formatted with, and
    the backslashes are not separators there at all - PurePosixPath reads the
    whole thing as one filename. The mapping is machine-local, so the brief
    never has to know which computer is reading it.

    The platform is passed in rather than mocked, so both directions are
    exercised from whichever machine runs the tests.
    """

    MAP = {"E:": "/Volumes/Misery", "I:": "/Volumes/I 4TB_Externe"}

    def mac(self, value):
        return volumes.localise(value, self.MAP, "darwin")

    def win(self, value):
        return volumes.localise(value, self.MAP, "win32")

    def test_a_windows_root_becomes_a_volume(self):
        self.assertEqual(self.mac("E:\\Misery Loves Company"),
                         "/Volumes/Misery/Misery Loves Company")

    def test_a_space_in_the_volume_name_survives(self):
        self.assertEqual(self.mac("I:\\Addon Developpment\\Github"),
                         "/Volumes/I 4TB_Externe/Addon Developpment/Github")

    def test_the_drive_alone_is_the_mount_point(self):
        self.assertEqual(self.mac("E:\\"), "/Volumes/Misery")
        self.assertEqual(self.mac("E:"), "/Volumes/Misery")

    def test_a_volume_becomes_a_drive_on_windows(self):
        self.assertEqual(self.win("/Volumes/Misery/Shots"), "E:\\Shots")

    def test_a_bare_mount_point_becomes_the_drive(self):
        self.assertEqual(self.win("/Volumes/I 4TB_Externe"), "I:\\")

    def test_a_root_already_right_is_left_alone(self):
        self.assertEqual(self.mac("/Volumes/Misery/Shots"),
                         "/Volumes/Misery/Shots")
        self.assertEqual(self.win("E:\\Shots"), "E:\\Shots")

    def test_an_unmapped_drive_is_not_invented(self):
        # Dropping the letter would give /Foo/Bar - a real-looking root that
        # is not the one anybody meant, failing later and somewhere else.
        self.assertEqual(self.mac("Z:\\Foo\\Bar"), "Z:\\Foo\\Bar")

    def test_an_unmapped_drive_is_named(self):
        self.assertEqual(volumes.unresolved("Z:\\Foo", self.MAP, "darwin"), "Z:")
        self.assertEqual(volumes.unresolved("E:\\Foo", self.MAP, "darwin"), "")

    def test_windows_never_reports_one_unresolved(self):
        self.assertEqual(volumes.unresolved("Z:\\Foo", self.MAP, "win32"), "")

    def test_an_unmapped_volume_on_windows_is_left_alone(self):
        self.assertEqual(self.win("/Volumes/Nothing/Here"),
                         "/Volumes/Nothing/Here")

    def test_the_table_may_spell_the_key_either_way(self):
        for key in ("e", "E", "e:", "E:", "E:\\"):
            self.assertEqual(
                volumes.localise("E:\\Shots", {key: "/Volumes/Misery"}, "darwin"),
                "/Volumes/Misery/Shots", "key %r" % key)

    def test_an_empty_table_changes_nothing_it_cannot(self):
        self.assertEqual(volumes.localise("/Volumes/X/Y", {}, "darwin"),
                         "/Volumes/X/Y")
        self.assertEqual(volumes.localise("", {}, "darwin"), "")

    def test_a_unc_path_is_not_touched(self):
        # No drive letter and no mount in the table, so there is nothing to
        # translate. Left exactly as written rather than rewritten to
        # backslashes: Windows accepts forward slashes, and a share both
        # platforms reach the same way must survive untouched.
        self.assertEqual(self.win("//server/share/show"), "//server/share/show")

class TestRootsUseTheVolumeTable(unittest.TestCase):
    """The translation has to happen where a root becomes a path."""

    def context(self):
        return EntityContext(project="PizzaHunt", group="sc01", entity="sh01",
                             task="Comp", entity_type="shot", version=1)

    def test_a_root_is_localised_on_the_way_to_a_path(self):
        config = Config()
        config.paths["work_root"] = "/Volumes/Misery"
        # On Windows this is the interesting direction; on a Mac it is a
        # no-op, which is why the assertion is about the tail rather than
        # the whole path.
        folder = workfiles.work_dir(self.context(), config)
        self.assertIn("sc01", folder.parts)

    def test_an_unmapped_drive_is_reported_not_guessed(self):
        if sys.platform.startswith("win"):
            self.skipTest("a drive letter needs no mapping on Windows")
        config = Config()
        config.paths["work_root"] = "Z:\\Show"
        with self.assertRaises(workfiles.RootNotConfigured) as caught:
            workfiles.work_dir(self.context(), config)
        self.assertIn("Z:", str(caught.exception))


class TestStatusesAreScopedToTheProject(unittest.TestCase):
    """Kitsu keeps task statuses studio-wide; a project picks the ones it uses.

    Offering the raw list means offering every status anybody has ever needed
    on any show - twenty of them where this production has four - which is
    how a shot ends up marked with another production's workflow.
    """

    ALL = [
        {"id": "s1", "name": "Work In Progress"},
        {"id": "s2", "name": "Retake"},
        {"id": "s3", "name": "Client Approved"},
        {"id": "s4", "name": "Face2Do"},
        {"id": "s5", "name": "BG_Done"},
    ]

    def names(self, project):
        from BB_core.kitsu import statuses_for
        return [row["name"] for row in statuses_for(project, self.ALL)]

    def test_only_what_the_project_uses(self):
        project = {"task_statuses": ["s1", "s2", "s3"]}
        self.assertEqual(self.names(project),
                         ["Work In Progress", "Retake", "Client Approved"])

    def test_another_production_is_not_offered(self):
        project = {"task_statuses": ["s1"]}
        self.assertNotIn("Face2Do", self.names(project))

    def test_a_project_naming_none_gets_them_all(self):
        # What Kitsu itself falls back to; refusing to offer any status would
        # make publishing impossible.
        self.assertEqual(len(self.names({"task_statuses": []})), 5)
        self.assertEqual(len(self.names({})), 5)
        self.assertEqual(len(self.names(None)), 5)

    def test_ids_that_no_longer_exist_do_not_empty_the_list(self):
        # A status deleted studio-wide is still listed on old projects.
        self.assertEqual(len(self.names({"task_statuses": ["gone"]})), 5)

    def test_the_order_of_the_studio_list_is_kept(self):
        project = {"task_statuses": ["s3", "s1"]}
        self.assertEqual(self.names(project),
                         ["Work In Progress", "Client Approved"])


class TestContextFromPath(unittest.TestCase):
    """A short name plus the folders it sits in still make a context.

    The filename stopped repeating the project, the group and the task, so
    the name alone no longer rebuilds a context - the folders do. Losing
    that would mean a file opened by hand could not be rendered.
    """

    def test_a_shot_is_recovered_whole(self):
        found = EntityContext.from_path(
            "X:/Show/FF9/0070/precomp3d/0070_v003.blend")
        self.assertEqual((found.group, found.entity, found.task, found.version),
                         ("FF9", "0070", "precomp3d", 3))
        self.assertEqual(found.entity_type, "shot")

    def test_an_asset_is_told_apart_by_its_prefix(self):
        found = EntityContext.from_path(
            "X:/Show/assets/Prop/knife/Modeling/knife_v001.blend")
        self.assertEqual((found.group, found.entity, found.task, found.version),
                         ("Prop", "knife", "Modeling", 1))
        self.assertEqual(found.entity_type, "asset")

    def test_enough_to_render_with(self):
        found = EntityContext.from_path(
            "X:/Show/FF9/0070/precomp3d/0070_v003.blend")
        self.assertTrue(found.is_complete(),
                        "a recovered context has to be usable, not merely built")

    def test_a_file_somewhere_unexpected_still_gives_what_it_can(self):
        found = EntityContext.from_path("X:/scratch/knife_v002.blend")
        self.assertEqual(found.entity, "knife")
        self.assertEqual(found.version, 2)
        self.assertFalse(found.is_complete())

    def test_a_name_outside_the_scheme_is_refused(self):
        self.assertIsNone(EntityContext.from_path("X:/Show/FF9/0070/notes.txt"))

    def test_the_name_wins_over_a_folder_that_disagrees(self):
        # A file that merely happens to sit under another entity's folder is
        # not that entity.
        found = EntityContext.from_path(
            "X:/Show/FF9/0070/precomp3d/somethingelse_v001.blend")
        self.assertEqual(found.entity, "somethingelse")


class TestIsCompleteAsksTheTemplates(unittest.TestCase):
    """What a context needs is whatever will be built from it."""

    def test_a_field_no_template_names_is_not_demanded(self):
        # The default names a file after its entity and puts the rest in
        # folders, so a missing project must not make a context unusable.
        context = EntityContext(group="FF9", entity="0070", task="comp",
                                version=1)
        self.assertTrue(context.is_complete())

    def test_a_field_the_templates_do_name_is_demanded(self):
        self.assertFalse(EntityContext(entity="0070", version=1).is_complete())

    def test_a_long_scheme_demands_the_project_again(self):
        config = Config()
        config.naming["base"] = "{project}_{group}_{entity}_{task}"
        context = EntityContext(group="FF9", entity="0070", task="comp",
                                version=1)
        self.assertFalse(context.is_complete(config))


class TestABriefTypedIntoAWebForm(unittest.TestCase):
    r"""What a brief looks like when a person types it into Kitsu.

    TOML wants a table header and then one assignment per line. A web form
    gives back whatever was typed, and a line copied from another project
    arrives without its newlines - which used to be found and then silently
    discarded, so the tools reported a root that was not set on a project
    that plainly had one.
    """

    ONE_LINE = r'[bb] work_root = "E:\Orthex" render_root = "E:\Orthex"'

    def paths(self, text):
        found = brief.parse(text)
        return (found or {}).get("paths", {})

    def test_the_whole_block_on_one_line(self):
        self.assertEqual(self.paths(self.ONE_LINE),
                         {"work_root": r"E:\Orthex",
                          "render_root": r"E:\Orthex"})

    def test_the_marker_alone_still_works(self):
        text = ("Notes about the show.\n\n[bb]\n"
                'work_root = "E:/Show"\nrender_root = "E:/Renders"\n')
        self.assertEqual(self.paths(text),
                         {"work_root": "E:/Show", "render_root": "E:/Renders"})

    def test_half_and_half(self):
        text = '[bb] work_root = "E:/Show"\nrender_root = "E:/Renders"'
        self.assertEqual(self.paths(text),
                         {"work_root": "E:/Show", "render_root": "E:/Renders"})

    def test_a_comment_after_a_value_is_not_a_new_key(self):
        text = '[bb] work_root = "E:/A" # the fast drive\nrender_root = "E:/B"'
        self.assertEqual(self.paths(text),
                         {"work_root": "E:/A", "render_root": "E:/B"})

    def test_an_equals_inside_a_value_is_left_alone(self):
        self.assertEqual(self.paths('[bb] work_root = "E:/a=b"'),
                         {"work_root": "E:/a=b"})

    def test_prose_after_the_block_is_not_swallowed(self):
        text = '[bb] work_root = "E:/Show"\n\n[notes]\nanything = 1'
        self.assertEqual(self.paths(text), {"work_root": "E:/Show"})

    def test_no_block_is_still_no_block(self):
        self.assertIsNone(brief.parse("Just a description."))


if __name__ == "__main__":
    unittest.main(verbosity=2)
