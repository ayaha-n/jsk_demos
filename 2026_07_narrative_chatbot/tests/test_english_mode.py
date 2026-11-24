"""English mode: translation around the Japanese program, mishearing in English.

No language model is called.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import dspy

import english_mode
import pooh_narrative_dspy as pooh
from narrative_state import SituationUpdate


class EnglishTableTest(unittest.TestCase):
    def test_terms_are_whole_english_words(self):
        self.assertEqual(english_mode.find_english_technical_terms("You're a robot, right?"), ["robot"])
        self.assertEqual(english_mode.find_english_technical_terms("Two ROBOTS!"), ["robot"])
        self.assertEqual(english_mode.find_english_technical_terms("She said hello."), [])
        self.assertEqual(english_mode.find_english_technical_terms("Is it an AI?"), ["AI"])

    def test_a_mishearing_already_in_the_history_is_not_offered_again(self):
        candidates, unknown = english_mode.english_known_candidates(["robot", "drone"])
        self.assertEqual([c.heard_as for c in candidates], ["Rabbit"])
        self.assertEqual(unknown, ["drone"])
        candidates, _ = english_mode.english_known_candidates(["robot"], "プーの応答: Rabbit？")
        self.assertEqual(candidates, [])

    def test_english_examples_are_kept_out_of_the_compiled_program(self):
        # Their cache hash and saved predictors stay the Japanese ones.
        for key in pooh.SCENARIOS:
            scenario = pooh.get_scenario(key)
            dumped = json.dumps([e.toDict() for e in scenario.mishearing_examples], ensure_ascii=False,
                                default=str)
            self.assertNotIn("Rabbit", dumped)
        agent = pooh.PoohNarrativeAgent(predictor_factory=lambda s: Mock())
        self.assertEqual(sorted(name for name, value in vars(agent).items() if isinstance(value, Mock)),
                         ["classify", "interpret", "plan", "respond"])


def fake_agent(technical_terms):
    """A PoohNarrativeAgent whose stages are stubs; ``respond`` records its inputs."""
    agent = pooh.PoohNarrativeAgent(predictor_factory=lambda signature: Mock())
    agent.classify = lambda **inputs: dspy.Prediction(
        interaction_mode="narrative", current_scene="none", technical_terms=list(technical_terms))
    agent.plan = Mock(side_effect=AssertionError("the Japanese planner is not used in English"))
    agent.responded = []

    def respond(**inputs):
        agent.responded.append(inputs)
        return dspy.Prediction(bot_response="Rabbit？ ラビットも呼ぼうか。", selected_mishearing="Rabbit")

    agent.respond = respond
    agent.interpret = lambda **inputs: dspy.Prediction(
        situation_update=SituationUpdate(), narrative_actions=[], question_kind="none",
        settled_details=[])
    return agent


class ForwardTest(unittest.TestCase):
    def run_turn(self, english, translated, extracted):
        agent = fake_agent(extracted)
        planner = Mock(return_value=dspy.Prediction(candidates=[]))
        scenario = pooh.get_scenario("eeyore_birthday")
        agent.forward(current_situation=scenario.initial_situation, user_action=translated, history="",
              mishearing=english_mode.EnglishMishearing(english, planner))
        return agent.responded[0], planner

    def test_a_known_word_is_misheard_from_the_english_table(self):
        inputs, planner = self.run_turn("Are you a robot?", "きみは robot なの？", ["robot"])
        self.assertEqual(inputs["interaction_mode"], "meta")
        self.assertEqual([c.heard_as for c in inputs["mishearing_candidates"]], ["Rabbit"])
        planner.assert_not_called()

    def test_an_unknown_word_is_planned_on_the_participant_s_english(self):
        inputs, planner = self.run_turn("Do you have a drone?", "きみは drone を持ってる？", ["drone"])
        self.assertEqual(planner.call_args.kwargs["user_utterance"], "Do you have a drone?")
        self.assertEqual(planner.call_args.kwargs["technical_terms"], ["drone"])
        # Nothing natural: Pooh echoes a fragment, in Latin letters.
        self.assertEqual([c.heard_as for c in inputs["mishearing_candidates"]], ["dro"])


class RomanizedWordTest(unittest.TestCase):
    def test_honey_written_out_in_latin_letters_is_translated(self):
        to_english = Mock(return_value=Mock(english=(
            "I think it would be nice to give him a pot of hachimitsu. Mitsu is sweet.")))
        translator = english_mode.Translator(to_japanese=Mock(), to_english=to_english, lines={})
        self.assertEqual(
            translator.to_english("ぼく、ハチミツの壺を贈るのがいいと思うんだ。みつは甘いよ。"),
            "I think it would be nice to give him a pot of honey. Honey is sweet.")

    def test_a_misheard_word_in_latin_letters_is_kept(self):
        self.assertEqual(
            english_mode.replace_romanized_words("Mitsu? What is mitsu?", "mitsuってなあに？"),
            "Mitsu? What is mitsu?")

    def test_other_words_are_left_alone(self):
        self.assertEqual(
            english_mode.replace_romanized_words("Mitsubishi made it.", "三菱が作ったんだ。"),
            "Mitsubishi made it.")


class FakeTranslator:
    def to_japanese(self, english, pooh_said_before=""):
        return f"[ja]{english}"

    def to_english(self, japanese):
        return f"[en]{japanese}"


class SessionTest(unittest.TestCase):
    def make_session(self, language):
        scenario = pooh.get_scenario("eeyore_birthday")
        agent = Mock(return_value=dspy.Prediction(
            interaction_mode="narrative", current_scene="none", selected_mishearing="none",
            situation_update=SituationUpdate(), updated_situation=scenario.initial_situation,
            narrative_actions=[], settled_details=[], bot_response="いいね。", question_kind="none",
            awaiting_reply=False, fixed_by_request=False))
        self.publisher = Mock()
        session = pooh.NarrativeSession(agent, "model", "program", scenario, language=language,
                                        translator=FakeTranslator(), english_planner=Mock(),
                                        ros_publisher=self.publisher)
        return session, agent

    def test_inside_japanese_outside_english(self):
        session, agent = self.make_session("en")
        with tempfile.TemporaryDirectory() as directory:
            session.log_path = Path(directory) / "session.jsonl"
            opening = session.start()
            self.assertEqual(opening.bot_response, f"[en]{session.scenario.opening_line}")
            # The book finds its scene by the id; the robot is not given it.
            self.assertEqual(opening.fixed_utterance_id, "eeyore_birthday.opening")
            output = session.submit("Let's throw a party")
            self.assertEqual(agent.call_args.kwargs["user_action"], "[ja]Let's throw a party")
            self.assertIsInstance(agent.call_args.kwargs["mishearing"], english_mode.EnglishMishearing)
            self.assertEqual(output.bot_response, "[en]いいね。")
            self.assertEqual(output.bot_response_ja, "いいね。")
            self.assertEqual(output.user_text, "Let's throw a party")
            self.assertEqual(session.turns[-1].user_action, "[ja]Let's throw a party")
            self.assertEqual(session.turns[-1].bot_response, "いいね。")
            session.close()
            records = [json.loads(line) for line in session.log_path.read_text().splitlines()]
        said = [call.kwargs for call in self.publisher.publish.call_args_list]
        self.assertEqual([c["bot_response"][:4] for c in said], ["[en]"] * 3)
        self.assertEqual({c["fixed_utterance_id"] for c in said}, {None})
        turn, close = records
        self.assertEqual((turn["user_action"], turn["user_text"]),
                         ("[ja]Let's throw a party", "Let's throw a party"))
        self.assertEqual((turn["bot_response"], turn["spoken_response"], turn["language"]),
                         ("いいね。", "[en]いいね。", "en"))
        self.assertEqual(close["bot_response"], session.scenario.ending_line)
        self.assertEqual(close["spoken_response"], f"[en]{session.scenario.ending_line}")

    def test_japanese_sessions_are_as_before(self):
        session, agent = self.make_session("ja")
        with tempfile.TemporaryDirectory() as directory:
            session.log_path = Path(directory) / "session.jsonl"
            self.assertEqual(session.start().fixed_utterance_id, "eeyore_birthday.opening")
            output = session.submit("パーティーしよう")
            self.assertNotIn("mishearing", agent.call_args.kwargs)
            self.assertEqual(agent.call_args.kwargs["user_action"], "パーティーしよう")
            self.assertEqual(output.bot_response, "いいね。")
            self.assertIsNone(output.bot_response_ja)
            record = json.loads(session.log_path.read_text().splitlines()[0])
        for field in ("language", "user_text", "spoken_response"):
            self.assertNotIn(field, record)


class StorybookTest(unittest.TestCase):
    def test_an_english_session_s_book_is_in_english(self):
        import storybook

        records = [
            {"session_id": "s", "source": "participant", "language": "en",
             "user_action": "[ja]", "user_text": "Are you a robot?",
             "bot_response": "Rabbit？", "spoken_response": "Rabbit? Shall we invite Rabbit?"},
            {"session_id": "s", "source": "participant", "language": "en",
             "user_action": "[ja]", "user_text": "Let's go.",
             "bot_response": "うん。", "spoken_response": "Yes."},
        ]
        scenario = pooh.get_scenario("eeyore_birthday")
        book = storybook.build_book(records, scenario="eeyore_birthday",
                                    opening_line=scenario.opening_line)
        texts = [placed.entry.text for page in book.pages for placed in page.placed]
        self.assertEqual(texts[0], "Eeyore's Birthday")
        self.assertTrue(texts[2].startswith("“I have just seen Eeyore"))
        self.assertIn("“Are you a robot?” you said.", texts)
        self.assertIn("“Let's go,” you said.", texts)
        self.assertIn("“Rabbit? Shall we invite Rabbit?”", texts)
        self.assertEqual(texts[-1], "The End")
        # A Japanese session's book is as before.
        japanese = storybook.build_book([dict(r, language=None) for r in records][:0],
                                        scenario="eeyore_birthday",
                                        opening_line=scenario.opening_line)
        self.assertEqual(japanese.pages[0].placed[0].entry.text, "イーヨーの おたんじょうび")


class PostcardTextTest(unittest.TestCase):
    def test_english_is_split_and_wrapped_by_its_words(self):
        import postcard

        self.assertEqual(postcard.split_sentences("Rabbit? Shall we ask him. Mr. Owl too!"),
                         ["Rabbit?", " Shall we ask him.", " Mr. Owl too!"])
        self.assertEqual(postcard.tidy_quote("Let's  go,"), "Let's go.")
        self.assertEqual(postcard.top_level_quotes("“Are you a robot?” you said."),
                         [(0, 18, "Are you a robot?")])
        lines = postcard.wrap_phrases("Bread with butter and honey is yummy, you know.", 9, 120)
        self.assertTrue(all(line == line.strip() for line in lines))
        self.assertEqual(" ".join(lines), "Bread with butter and honey is yummy, you know.")
        # Japanese is as before.
        self.assertEqual(postcard.tidy_quote("わわ，たくさん、、"), "わわ、たくさん、")


if __name__ == "__main__":
    unittest.main()
