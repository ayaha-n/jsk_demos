"""Footprints response windows start after the preceding beat, not in parallel."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from footprints_events import FootprintsEventController, NAMING_TOPIC, APPEARANCE_UNRESOLVED
from narrative_events import SettledDetail


class FootprintsTimingTests(unittest.TestCase):
    def controller(self, ask=90, naming=10, appearance=90, following=10):
        self.now = 0.0
        return FootprintsEventController(ask, naming, appearance, following, 30, 30, 45,
                                         clock=lambda: self.now, speech_seconds=lambda text: 10)

    def test_naming_waits_for_question_and_its_speech_even_if_its_delay_is_shorter(self):
        controller = self.controller()
        controller.begin(5)
        self.now = 94
        self.assertIsNone(controller.pop_due_event())
        self.now = 95
        self.assertEqual(controller.pop_due_event().event_id, "ask_name")
        self.now = 114
        self.assertIsNone(controller.pop_due_event())
        self.now = 115
        self.assertEqual(controller.pop_due_event().event_id, "name_creature")

    def test_following_waits_until_after_appearance_question_and_its_speech(self):
        controller = self.controller()
        controller.begin(0)
        controller.observe_settled_details([SettledDetail(topic=NAMING_TOPIC, value="ぽんぽん")])
        self.now = 89
        self.assertIsNone(controller.pop_due_event())
        self.now = 90
        self.assertEqual(controller.pop_due_event().event_id, "ask_appearance")
        self.now = 109
        self.assertIsNone(controller.pop_due_event())
        self.now = 110
        self.assertEqual(controller.pop_due_event().event_id, "start_following")

    def test_organic_appearance_discussion_can_replace_the_fixed_question(self):
        controller = self.controller()
        controller.begin(0)
        controller.observe_settled_details([SettledDetail(topic=NAMING_TOPIC, value="ぽんぽん")])
        self.now = 20
        controller.observe_activity(8)
        controller.observe_settled_details([SettledDetail(topic=APPEARANCE_UNRESOLVED, value="丸くて青い")])
        self.now = 37
        self.assertIsNone(controller.pop_due_event())
        self.now = 38
        self.assertEqual(controller.pop_due_event().event_id, "start_following")
        self.assertNotIn("ask_appearance", controller.state.completed_event_ids)

    def test_intro_invitation_does_not_skip_the_name_question(self):
        controller = self.controller()
        controller.begin(0)
        # Held back, so not recorded either: the picture book must not start
        # the following scene here and again when it really happens.
        self.assertEqual(controller.observe_actions(["started_following", "noticed_second_animal"]), [])
        self.assertFalse(controller.state.following_started)
        self.assertNotIn("second_animal", controller.state.completed_event_ids)
        self.assertEqual(controller.pop_due_event(follow_up=True).event_id, "ask_name")
        self.assertFalse(controller.state.named)
        self.now = 19
        self.assertIsNone(controller.pop_due_event())
        self.now = 20
        self.assertEqual(controller.pop_due_event().event_id, "name_creature")
        self.now = 119
        self.assertIsNone(controller.pop_due_event())
        self.now = 120
        self.assertEqual(controller.pop_due_event().event_id, "ask_appearance")
        self.now = 140
        self.assertEqual(controller.pop_due_event().event_id, "start_following")
        self.now = 180
        self.assertEqual(controller.pop_due_event().event_id, "second_animal")

    def test_tracking_request_after_naming_still_asks_appearance_first(self):
        controller = self.controller()
        controller.begin(0)
        controller.observe_settled_details([SettledDetail(topic=NAMING_TOPIC, value="ぽんぽん")])
        self.assertEqual(controller.observe_actions(["started_following", "noticed_fourth_animal"]), [])
        self.assertFalse(controller.state.following_started)
        self.assertNotIn("fourth_animal", controller.state.completed_event_ids)
        self.assertEqual(controller.pop_due_event(follow_up=True).event_id, "ask_appearance")

    def test_early_participant_naming_cancels_the_automatic_naming(self):
        controller = self.controller(ask=0, naming=60)
        controller.begin(0)
        self.assertEqual(controller.pop_due_event().event_id, "ask_name")
        self.now = 15
        controller.observe_settled_details([SettledDetail(topic=NAMING_TOPIC, value="ぽんぽん")])
        self.now = 75
        self.assertIsNone(controller.pop_due_event())
        self.assertEqual(controller.state.name, "ぽんぽん")

class FootprintsSpeechTimingTests(unittest.TestCase):
    def test_overdue_follow_up_is_preserved(self):
        now = [0.0]
        controller = FootprintsEventController(30, 60, 20, 60, 30, 30, 45,
                      clock=lambda: now[0], quiet_seconds=12, speech_seconds=lambda text: 10)
        controller.begin(0)
        now[0] = 40
        controller.observe_activity(10)
        self.assertEqual(controller.pop_due_event(follow_up=True).event_id, 'ask_name')

    def test_real_audio_end_replaces_the_estimate_before_starting_next_wait(self):
        now = [0.0]
        controller = FootprintsEventController(30, 60, 20, 60, 30, 30, 45,
                      clock=lambda: now[0], quiet_seconds=12, speech_seconds=lambda text: 10)
        controller.begin(5)  # opening expected to finish at t=5
        now[0] = 25         # actual opening finished at t=25
        controller.confirm_speech_finished()
        now[0] = 54
        self.assertIsNone(controller.pop_due_event())
        now[0] = 55
        self.assertEqual(controller.pop_due_event().event_id, 'ask_name')
        # The session can update its speech estimate again after fixed-event generation.
        controller.observe_activity(10)
        now[0] = 95         # actual question finishes at 95
        controller.confirm_speech_finished()
        now[0] = 154
        self.assertIsNone(controller.pop_due_event())
        now[0] = 155
        self.assertEqual(controller.pop_due_event().event_id, 'name_creature')


class EeyoreSpeechTimingTests(unittest.TestCase):
    def test_web_audio_correction_is_shared_but_ros_estimates_are_unchanged(self):
        from eeyore_birthday_events import HoneyGiftEventController
        now = [0.0]
        controller = HoneyGiftEventController(30, 30, 10, clock=lambda: now[0],
                                               speech_seconds=lambda text: 5)
        controller.begin(5)
        now[0] = 35
        # Without a browser report (ROS), the original estimate still applies.
        self.assertEqual(controller.seconds_until_due(), 0)
        # A Web player reports the opening actually ended at t=35.
        controller.confirm_speech_finished()
        self.assertEqual(controller.seconds_until_due(), 30)
        now[0] = 65
        self.assertIsNotNone(controller.pop_due_event())


if __name__ == "__main__":
    unittest.main()
