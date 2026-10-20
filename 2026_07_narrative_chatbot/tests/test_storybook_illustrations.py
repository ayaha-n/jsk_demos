"""Illustration planning tests: what the scene summary is given.

No image or text model is called.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import storybook
from storybook import StoryTurn
from storybook_illustrations import scene_context

EEYORE = storybook.load_story_config("eeyore_birthday")


class SceneContextTest(unittest.TestCase):
    def test_what_the_reader_was_seen_doing_is_in_the_scene(self):
        turns = [
            StoryTurn("session_open", "やあ。"),
            StoryTurn("world_event", "そうだ！", world_event_id="honey_gift_committed"),
            StoryTurn("observed", "", observed_actions=("参加者が風船をプーに差し出した",)),
            StoryTurn("participant", "ありがとう。", user_text="「どうぞ」と言って、風船をプーに差し出す"),
            StoryTurn("observed", "", observed_actions=("参加者がプーの左手をなでた",)),
        ]
        context = scene_context(EEYORE, "honey_gift_committed", turns)
        self.assertEqual(context["current_scene"][1:], [
            {"reader_did": ["参加者が風船をプーに差し出した"]},
            {"reader": "「どうぞ」と言って、風船をプーに差し出す", "bear": "ありがとう。"},
            {"reader_did": ["参加者がプーの左手をなでた"]},
        ])
        self.assertEqual(context["latest_participant_dialogue"][-1],
                         {"reader_did": ["参加者がプーの左手をなでた"]})

    def test_the_reader_s_hand_may_be_drawn(self):
        self.assertIn("hand", EEYORE.participant_presence)
        self.assertNotIn("no face, body or hands", EEYORE.participant_presence)


if __name__ == "__main__":
    unittest.main()
