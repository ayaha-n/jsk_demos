"""scripts/run.py: the command it builds from the menu's settings."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import run as launch
import pooh_narrative_dspy
import web_server


class LaunchTest(unittest.TestCase):
    def test_all_scenarios_keep_default_first_and_load_each_only_once(self):
        from scenarios import SCENARIOS, selected_scenario_keys

        args = pooh_narrative_dspy.parse_args([
            "--scenarios", "all", "footprints", "--ros-relay", "--start-on-mode",
        ])
        keys = selected_scenario_keys(args.scenarios[0], args.scenarios[1:])
        self.assertEqual(keys[0], "eeyore_birthday")
        self.assertEqual(set(keys), set(SCENARIOS))
        self.assertEqual(len(keys), len(SCENARIOS))
        self.assertEqual(selected_scenario_keys("eeyore_birthday", []), ["eeyore_birthday"])
        self.assertEqual(selected_scenario_keys("footprints", ["tea_party", "footprints"]),
                         ["footprints", "tea_party"])
        with self.assertRaises(ValueError):
            selected_scenario_keys("footprints", ["unknown"])

    def test_unified_scenarios_preserve_order_and_single_scenario_support(self):
        from scenarios import selected_scenario_keys

        args = pooh_narrative_dspy.parse_args([
            "--scenarios", "footprints", "eeyore_birthday", "footprints",
            "--ros-relay", "--start-on-mode",
        ])
        self.assertEqual(selected_scenario_keys(args.scenarios[0], args.scenarios[1:]),
                         ["footprints", "eeyore_birthday"])
        args = pooh_narrative_dspy.parse_args(["--mode", "compile", "--scenario", "footprints"])
        self.assertEqual(args.scenario, "footprints")
        self.assertIsNone(args.scenarios)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.local = Path(temporary.name) / "run.json"
        self.options = launch.available_options()
        self.crowd = launch.crowdsourcing_scenarios()

    def settings(self, target="web", **changes):
        settings = launch.load_settings(local_path=self.local)
        settings["target"] = target
        settings[target].update(changes)
        return settings

    def command(self, settings):
        return launch.build_command(settings, self.options, self.crowd)

    def test_the_shared_web_defaults_start_a_server_web_server_accepts(self):
        settings = self.settings()
        script, argv = self.command(settings)
        self.assertEqual(script.name, "web_server.py")
        args = web_server.parse_args(argv)
        self.assertEqual(args.port, settings["web"]["ports"][settings["web"]["scenario"]])
        self.assertTrue(args.crowdsourcing)

    def test_the_shared_robot_defaults_start_the_robot_s_chat_without_the_web_ui(self):
        settings = self.settings("robot")
        script, argv = self.command(settings)
        self.assertEqual(script.name, "pooh_narrative_dspy.py")
        args = pooh_narrative_dspy.parse_args(argv)
        self.assertTrue(args.ros_relay)
        self.assertEqual(args.scenario, settings["robot"]["scenario"])
        self.assertEqual(args.extra_scenario, settings["robot"]["extra_scenarios"])
        # As pooh_body's pooh_narrative.launch starts it by default.
        self.assertTrue(args.start_on_mode and args.no_idle_close and args.storybook_generate)
        self.assertFalse(args.storybook_eink)
        self.assertTrue(args.postcards)
        self.assertNotIn("--crowdsourcing", argv)

    def test_extra_scenarios_only_with_start_on_mode_and_never_the_main_one(self):
        settings = self.settings("robot", start_on_mode=False)
        self.assertNotIn("--extra-scenario", self.command(settings)[1])
        settings = self.settings("robot", scenario="footprints", extra_scenarios=["footprints", "tea_party"],
                                 start_on_mode=True)
        self.assertEqual(pooh_narrative_dspy.parse_args(self.command(settings)[1]).extra_scenario,
                         ["tea_party"])

    def test_the_menu_switches_target_and_toggles_extra_scenarios(self):
        settings = self.settings()
        scenarios = ["eeyore_birthday", "footprints", "tea_party"]
        items = launch.menu_items(settings, self.options, self.crowd, scenarios)
        self.assertEqual(items[0]["value"], "Web UI")
        launch.change(settings, items[0], scenarios)
        items = launch.menu_items(settings, self.options, self.crowd, scenarios)
        self.assertEqual(items[0]["value"], "ロボット")
        self.assertFalse(any(item["key"] == "crowdsourcing" for item in items))
        extra = next(item for item in items if item["key"] == "extra:tea_party")
        launch.change(settings, extra, scenarios)
        self.assertIn("tea_party", settings["robot"]["extra_scenarios"])

    def test_the_postcards_paper_is_chosen_in_the_menu(self):
        scenarios = ["eeyore_birthday", "footprints", "tea_party"]
        for target, parse in (("web", web_server.parse_args), ("robot", pooh_narrative_dspy.parse_args)):
            settings = self.settings(target, postcards=True)
            paper = next(item for item in launch.menu_items(settings, self.options, self.crowd, scenarios)
                         if item["key"] == "postcard_size")
            self.assertEqual(paper["value"], "L判")  # SELPHY's L by default
            self.assertEqual(parse(self.command(settings)[1]).postcard_size, "l")
            launch.change(settings, paper, scenarios)
            self.assertEqual(parse(self.command(settings)[1]).postcard_size, "hagaki")
            settings[target]["postcards"] = False  # no paper without postcards
            self.assertNotIn("--postcard-size", self.command(settings)[1])

    def test_the_passphrase_is_kept_locally_not_in_the_shared_defaults(self):
        self.assertNotIn("passphrase", DEFAULTS_TEXT)
        launch.save_local(self.settings(passphrase="アイコトバ"), self.local)
        args = web_server.parse_args(self.command(self.settings())[1])
        self.assertEqual(args.passphrase, "アイコトバ")

    def test_only_options_this_version_has_and_crowdsourcing_only_where_it_works(self):
        settings = self.settings(scenario="tea_party", port=8082, postcards=True, ros_relay=True)
        options = {**self.options, "web": self.options["web"] - {"postcards"}}
        argv = launch.build_command(settings, options, self.crowd)[1]
        self.assertNotIn("--postcards", argv)
        self.assertNotIn("--crowdsourcing", argv)
        self.assertIn("--ros-relay", argv)
        web_server.parse_args(argv)

    def test_warnings(self):
        settings = self.settings(scenario="eeyore_birthday", port=8081, passphrase="")
        warnings = launch.warnings_for(settings, self.crowd, in_use=lambda port, host: port == 8081)
        self.assertEqual(len(warnings), 3)  # port taken, no speech server, no passphrase
        quiet = launch.warnings_for(self.settings(passphrase="x", tts=False), self.crowd,
                                    in_use=lambda port, host: False)
        self.assertEqual(quiet, [])
        robot = launch.warnings_for(self.settings("robot"), self.crowd, in_use=lambda port, host: False)
        self.assertEqual(len(robot), 1)  # the bridge does not answer

    def test_the_robot_is_not_started_twice(self):
        self.assertIsNone(launch.robot_refusal(running=lambda: []))
        refusal = launch.robot_refusal(running=lambda: [1234])
        self.assertIn("1234", refusal)  # e.g. pooh_operator.launch use_narrative:=true
        # Checked for the robot, and for a Web UI that also talks to it.
        self.assertTrue(launch.uses_robot(self.settings("robot")))
        self.assertTrue(launch.uses_robot(self.settings(ros_relay=True)))
        self.assertFalse(launch.uses_robot(self.settings(ros_relay=False)))


DEFAULTS_TEXT = launch.DEFAULTS_PATH.read_text(encoding="utf-8")

if __name__ == "__main__":
    unittest.main()
