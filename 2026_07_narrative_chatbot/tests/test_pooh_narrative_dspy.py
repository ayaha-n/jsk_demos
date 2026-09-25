"""LLM/APIを呼ばずに行う最小回帰テスト。"""

import ast
import json
import sys
import types
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch
from types import SimpleNamespace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


# DSPy未導入の開発環境でも純粋関数と状態定義を検査できる最小stub。
class _Field:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _Example:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def with_inputs(self, *args):
        self.inputs = args
        return self

    def toDict(self):
        return vars(self)


class _Module:
    def __init__(self, *args, **kwargs):
        self.demos = []

    def predictors(self):
        return []


fake = types.ModuleType("dspy")
fake.Signature = object
fake.Module = _Module
fake.Prediction = lambda **kwargs: SimpleNamespace(**kwargs)
fake.InputField = _Field
fake.OutputField = _Field
fake.Example = _Example
fake.Predict = _Module
fake.ChainOfThought = _Module
fake.LM = _Module
fake.configure = lambda **kwargs: None
fake.context = lambda **kwargs: __import__("contextlib").nullcontext()
teleprompt = types.ModuleType("dspy.teleprompt")
teleprompt.BootstrapFewShot = _Module
sys.modules.setdefault("dspy", fake)
sys.modules.setdefault("dspy.teleprompt", teleprompt)

import pooh_narrative_dspy as pooh
import pooh_examples
from mishearing_cases import (
    MISHEARING_CASES,
    UNCERTAIN_RESPONSE_TEMPLATES,
    make_uncertain_echo,
    normalize_term,
    uncertain_candidate_for,
)
from narrative_state import (
    NarrativeSituation,
    SituationUpdate,
    apply_situation_update,
    relevant_preferences,
)
from narrative_events import (
    BALLOON_COLOR_UNRESOLVED,
    BLOCKED_ACCESS_EVENT,
    EMPTY_JAR_UNRESOLVED,
    GIFT_DECISION_UNRESOLVED,
    GIFT_UNRESOLVED,
    HONEY_EATEN_RESPONSE,
    HONEY_GIFT_COMMITTED_EVENT,
    HONEY_GIFT_COMMITTED_RESPONSE,
    HONEY_PREPARATION_UNRESOLVED,
    HONEY_TASTED_EVENT,
    HONEY_TASTED_FOLLOW_UP_RESPONSE,
    HONEY_TASTED_RESPONSE,
    RIBBON_COLOR_UNRESOLVED,
    STORY_WRAP_UP_EVENT,
    STORY_WRAP_UP_RESPONSE,
    FIXED_UTTERANCES,
    POOH_GIFT_TOPIC,
    HoneyGiftEventController,
    SettledDetail,
    RequiredNarrativeEvent,
    WorldEvent,
    fixed_utterance_id,
)



class RegressionTests(unittest.TestCase):
    def test_chat_passes_opening_and_previous_reply_to_agent(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        reply = "ぼくなら、ハチミツのつぼがいいな。"
        agent = Mock(return_value=SimpleNamespace(
            interaction_mode="narrative",
            current_scene="1a",
            narrative_actions=[],
            bot_response=reply,
            updated_situation=scenario.initial_situation,
        ))
        answers = iter(["", "迷うな", "ほかには？", "exit"])
        with patch("builtins.print"), patch.object(pooh, "append_log"):
            pooh.run_chat(
                agent,
                "test-model",
                "test-program",
                scenario,
                input_fn=lambda _prompt: next(answers),
            )
        self.assertEqual(agent.call_count, 2)
        first = agent.call_args_list[0].kwargs["history"]
        second = agent.call_args_list[1].kwargs["history"]
        self.assertIn(scenario.opening_line, first)
        self.assertNotIn("参加者の生入力:", first)
        self.assertIn(scenario.opening_line, second)
        self.assertIn("参加者の生入力: 迷うな", second)
        self.assertEqual(agent.call_args_list[0].kwargs["previous_bot_response"], "")
        self.assertEqual(agent.call_args_list[1].kwargs["previous_bot_response"], reply)
        self.assertIn(reply, second)
        # The opening situation has no balloon-color topic pending yet, so
        # nothing is surfaced on this first call.
        self.assertEqual(agent.call_args_list[0].kwargs["pooh_preferences"], "")

    def test_pooh_preferences_is_filtered_by_current_unresolved_items(self):
        eeyore = pooh.get_scenario("eeyore_birthday")
        tea_party = pooh.get_scenario("tea_party")
        self.assertEqual(tea_party.pooh_preferences, {})
        self.assertIn(BALLOON_COLOR_UNRESOLVED, eeyore.pooh_preferences)
        self.assertIn(RIBBON_COLOR_UNRESOLVED, eeyore.pooh_preferences)

        # Not surfaced when the topic isn't currently unresolved.
        self.assertEqual(
            relevant_preferences(eeyore.initial_situation, eeyore.pooh_preferences),
            "",
        )
        # Surfaced once the topic is actually pending.
        situation_with_color_pending = eeyore.initial_situation.model_copy(
            update={"unresolved": [BALLOON_COLOR_UNRESOLVED]},
        )
        self.assertIn(
            "青",
            relevant_preferences(situation_with_color_pending, eeyore.pooh_preferences),
        )
        # Every derived response example demonstrates the same filtering.
        for example in eeyore.response_examples:
            self.assertEqual(
                example.pooh_preferences,
                relevant_preferences(example.current_situation, eeyore.pooh_preferences),
            )

    def test_source_is_valid_python(self):
        ast.parse(Path(pooh.__file__).read_text(encoding="utf-8"))

    def test_narrative_relay_publisher_sends_expected_json(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        sent = []

        class FakeSocket:
            def sendall(self, data):
                sent.append(data)

            def close(self):
                pass

        publisher = pooh.NarrativeRelayPublisher("127.0.0.1", 8765, scenario.key)
        publisher._socket = FakeSocket()  # bypass the real TCP connection
        publisher.publish(
            bot_response="うん、そうしよう。",
            narrative_actions=["commit_honey_jar_gift"],
            scene_id="1a",
            source="participant",
        )

        self.assertEqual(len(sent), 1)
        payload = json.loads(sent[0].decode("utf-8"))
        self.assertEqual(payload["scenario"], "eeyore_birthday")
        self.assertEqual(payload["bot_response"], "うん、そうしよう。")
        self.assertEqual(payload["narrative_actions"], ["commit_honey_jar_gift"])
        self.assertEqual(payload["scene_id"], "1a")
        self.assertEqual(payload["source"], "participant")
        self.assertEqual(payload["type"], "narrative_response")
        self.assertNotIn("world_event_id", payload)

    def test_narrative_relay_publisher_includes_world_event_id_when_given(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        sent = []

        class FakeSocket:
            def sendall(self, data):
                sent.append(data)

            def close(self):
                pass

        publisher = pooh.NarrativeRelayPublisher("127.0.0.1", 8765, scenario.key)
        publisher._socket = FakeSocket()
        publisher.publish(
            bot_response="…",
            narrative_actions=[],
            scene_id="3",
            source="world_event",
            world_event_id="pooh_ate_honey",
        )

        payload = json.loads(sent[0].decode("utf-8"))
        self.assertEqual(payload["world_event_id"], "pooh_ate_honey")

    def test_narrative_relay_publisher_includes_performance_cue_when_given(self):
        sent = []

        class FakeSocket:
            def sendall(self, data):
                sent.append(data)

            def close(self):
                pass

        publisher = pooh.NarrativeRelayPublisher("127.0.0.1", 8765, "eeyore_birthday")
        publisher._socket = FakeSocket()
        publisher.publish(
            bot_response="またね。",
            narrative_actions=[],
            scene_id="none",
            source="session_close",
            performance_cue="ending",
            fixed_utterance_id="eeyore_birthday.ending",
        )

        payload = json.loads(sent[0].decode("utf-8"))
        self.assertEqual(payload["source"], "session_close")
        self.assertEqual(payload["performance_cue"], "ending")
        self.assertEqual(payload["fixed_utterance_id"], "eeyore_birthday.ending")

    def test_fixed_utterance_registry_has_unique_ids_text_and_wav_names(self):
        self.assertEqual(
            len({spec["text"] for spec in FIXED_UTTERANCES.values()}),
            len(FIXED_UTTERANCES),
        )
        for utterance_id, spec in FIXED_UTTERANCES.items():
            self.assertEqual(spec["wav"], utterance_id + ".wav")
            self.assertEqual(fixed_utterance_id(spec["text"]), utterance_id)

    def test_session_publishes_opening_and_ending_once(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        publisher = Mock()
        session = pooh.NarrativeSession(
            Mock(),
            "test-model",
            "test-program",
            scenario,
            ros_publisher=publisher,
            session_id="test-session",
        )

        opening = session.start()
        self.assertEqual(opening.bot_response, scenario.opening_line)
        self.assertEqual(opening.performance_cue, "opening")
        self.assertEqual(opening.fixed_utterance_id, "eeyore_birthday.opening")
        self.assertIsNone(session.start())

        ending = session.close()
        self.assertEqual(ending.bot_response, scenario.ending_line)
        self.assertEqual(ending.performance_cue, "ending")
        self.assertEqual(ending.fixed_utterance_id, "eeyore_birthday.ending")
        self.assertIsNone(session.close())
        self.assertEqual(publisher.publish.call_count, 2)
        self.assertEqual(
            publisher.publish.call_args_list[0].kwargs["performance_cue"],
            "opening",
        )
        self.assertEqual(
            publisher.publish.call_args_list[1].kwargs["performance_cue"],
            "ending",
        )
        self.assertEqual(
            publisher.publish.call_args_list[0].kwargs["fixed_utterance_id"],
            "eeyore_birthday.opening",
        )
        public = opening.to_dict()
        self.assertEqual(public["source"], "session_open")
        self.assertEqual(public["situation"]["place"], scenario.initial_situation.place)
        self.assertNotIn("turn", public)

    def test_narrative_sessions_keep_state_and_timers_separate(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        first_controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: 0.0)
        second_controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: 0.0)
        first = pooh.NarrativeSession(
            Mock(), "model", "program", scenario,
            event_controller=first_controller,
        )
        second = pooh.NarrativeSession(
            Mock(), "model", "program", scenario,
            event_controller=second_controller,
        )
        first.start()
        second.start()

        first.controller.observe_actions(["commit_honey_jar_gift"])

        self.assertEqual(first.controller.state.gift_status, "committed")
        self.assertEqual(second.controller.state.gift_status, "undecided")
        self.assertIsNot(first.current_situation, second.current_situation)

    def test_session_marks_generated_exit_response_as_the_ending_cue(self):
        scenario = pooh.get_scenario("tea_party")
        agent = Mock(return_value=SimpleNamespace(
            interaction_mode="exit",
            current_scene="none",
            narrative_actions=[],
            bot_response="うん、わかったよ。またね。",
            updated_situation=scenario.initial_situation,
        ))
        publisher = Mock()
        session = pooh.NarrativeSession(
            agent, "model", "program", scenario, ros_publisher=publisher,
        )
        session.start()
        with patch.object(pooh, "append_log"):
            output = session.submit("もう終わりにしたい")

        self.assertTrue(session.ended)
        self.assertEqual(output.performance_cue, "ending")
        self.assertEqual(output.bot_response, "うん、わかったよ。またね。")
        self.assertIsNone(session.close())
        self.assertEqual(publisher.publish.call_count, 2)

    def test_session_follow_up_uses_follow_up_line_after_answer(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        now = [0.0]
        controller = HoneyGiftEventController(
            30.0, 30.0, 10.0, clock=lambda: now[0], quiet_seconds=12.0,
        )
        agent = Mock(side_effect=[
            SimpleNamespace(
                interaction_mode="narrative",
                current_scene="none",
                narrative_actions=[],
                bot_response=line,
                updated_situation=scenario.initial_situation,
            )
            for line in ("ケーキ、いいね。", "アザミのケーキ、すてきだね。")
        ])
        publisher = Mock()
        session = pooh.NarrativeSession(
            agent, "model", "program", scenario,
            event_controller=controller, ros_publisher=publisher,
        )
        session.start()
        now[0] = 29.0
        with patch.object(pooh, "append_log") as log:
            session.submit("ケーキを作るよ")
            self.assertIsNone(session.follow_up())
            now[0] = 35.0
            answer = session.submit("アザミのケーキにする")
            self.assertIsNone(session.poll())
            event = session.follow_up()

        self.assertEqual(answer.source, "participant")
        self.assertEqual(event.world_event_id, "honey_gift_committed")
        self.assertEqual(
            event.fixed_utterance_id,
            "eeyore_birthday.honey_gift_committed.follow_up",
        )
        self.assertNotEqual(event.bot_response, HONEY_GIFT_COMMITTED_RESPONSE)
        self.assertTrue(event.bot_response.endswith("贈ることにしよう。きっと喜ぶね。"))
        self.assertTrue(log.call_args_list[-1].args[2]["event_follow_up"])
        self.assertEqual(
            publisher.publish.call_args_list[-1].kwargs["world_event_id"],
            "honey_gift_committed",
        )

    def test_no_follow_up_while_pooh_awaits_the_participant_reply(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        now = [0.0]
        controller = HoneyGiftEventController(
            30.0, 30.0, 10.0, clock=lambda: now[0], quiet_seconds=12.0,
        )
        replies = [
            ("どんな色の風船がいい？", True),
            ("青にしよう！晴れた空みたいだもの。", False),
        ]
        agent = Mock(side_effect=[
            SimpleNamespace(
                interaction_mode="narrative", current_scene="none", narrative_actions=[],
                bot_response=line, awaiting_reply=awaiting,
                updated_situation=scenario.initial_situation,
            )
            for line, awaiting in replies
        ])
        session = pooh.NarrativeSession(
            agent, "model", "program", scenario, event_controller=controller,
        )
        session.start()
        now[0] = 35.0
        with patch.object(pooh, "append_log"):
            session.submit("風船はどう？")
            self.assertTrue(session.awaiting_reply)
            self.assertIsNone(session.follow_up())
            session.submit("青がいいな")
            self.assertFalse(session.awaiting_reply)
            event = session.follow_up()

        self.assertEqual(event.world_event_id, "honey_gift_committed")

    def test_runtime_replacement_drops_decisions_made_for_the_original_line(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: 0.0)
        changed = scenario.initial_situation.model_copy(update={"purpose": "別の目的"})
        agent = Mock(return_value=SimpleNamespace(
            interaction_mode="narrative", current_scene="1a",
            narrative_actions=["commit_honey_jar_gift"],
            settled_details=[SettledDetail(topic="飲み物", value="お茶")],
            bot_response="お茶にしようか。きみはどう思う？", awaiting_reply=True,
            updated_situation=changed,
        ))
        session = pooh.NarrativeSession(
            agent, "model", "program", scenario, event_controller=controller,
        )
        session.start()
        with patch.object(pooh, "append_log") as log:
            output = session.submit("うーん、わからないな")

        self.assertEqual(output.bot_response, pooh.HONEY_PROPOSAL_FALLBACK_RESPONSE)
        self.assertEqual(output.narrative_actions, ["propose_honey_jar_gift"])
        # Only the guard's own proposal counts, not the dropped model commit.
        self.assertNotIn("飲み物", controller.state.settled_details)
        self.assertEqual(output.updated_situation.purpose, scenario.initial_situation.purpose)
        self.assertTrue(log.call_args.args[2]["response_replaced"])

    def _decline_session(self, prepare):
        scenario = pooh.get_scenario("eeyore_birthday")
        now = [0.0]
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: now[0])
        prepare(controller, now)
        invented = scenario.initial_situation.model_copy(
            update={"purpose": "ケーキと風船を用意することに決めた"},
        )
        agent = Mock(return_value=SimpleNamespace(
            interaction_mode="narrative", current_scene="1b",
            narrative_actions=["decline_honey_jar_gift", "not_give_empty_jar"],
            settled_details=[SettledDetail(topic=GIFT_UNRESOLVED, value="ケーキ")],
            bot_response="そうだね、ケーキだけにしよう！", awaiting_reply=False,
            updated_situation=invented,
        ))
        session = pooh.NarrativeSession(
            agent, "model", "program", scenario, event_controller=controller,
        )
        session.start()
        with patch.object(pooh, "append_log"):
            output = session.submit("ハチミツはいらないよ、ケーキにしよう")
        return scenario, controller, output

    def test_declined_honey_jar_is_kept_with_the_fixed_line(self):
        scenario, controller, output = self._decline_session(
            lambda controller, now: controller.observe_actions(["propose_honey_jar_gift"])
        )

        self.assertEqual(output.bot_response, pooh.HONEY_GIFT_KEPT_RESPONSE)
        self.assertEqual(output.fixed_utterance_id, "eeyore_birthday.honey_gift_kept")
        self.assertEqual(controller.state.gift_status, "committed")
        self.assertIn("プーがあげるもの：ハチミツの入った壺", output.updated_situation.decided)
        # The details were read from the discarded line, so none of them apply.
        self.assertNotIn("イーヨーに何をあげるか：ケーキ", output.updated_situation.decided)
        self.assertEqual(output.updated_situation.purpose, scenario.initial_situation.purpose)

    def test_decline_is_ignored_when_the_honey_jar_is_not_pooh_gift(self):
        def eaten(controller, now):
            controller.observe_actions(["commit_honey_jar_gift"])
            now[0] = 30.0
            controller.pop_due_event()
            now[0] = 40.0
            controller.pop_due_event()

        for prepare in (lambda controller, now: None, eaten):
            _, _, output = self._decline_session(prepare)
            self.assertEqual(output.bot_response, "そうだね、ケーキだけにしよう！")
            self.assertIsNone(output.fixed_utterance_id)

    def test_generated_fixed_line_carries_its_utterance_id(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        agent = Mock(return_value=SimpleNamespace(
            interaction_mode="narrative", current_scene="1b", narrative_actions=[],
            settled_details=[], bot_response=pooh.HONEY_GIFT_KEPT_RESPONSE,
            awaiting_reply=False, updated_situation=scenario.initial_situation,
        ))
        session = pooh.NarrativeSession(agent, "model", "program", scenario)
        session.start()
        with patch.object(pooh, "append_log"):
            output = session.submit("ハチミツはいらないよ")

        self.assertEqual(output.fixed_utterance_id, "eeyore_birthday.honey_gift_kept")

    def test_runtime_replacement_is_not_awaiting_a_reply(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        agent = Mock(return_value=SimpleNamespace(
            interaction_mode="narrative", current_scene="1a", narrative_actions=[],
            bot_response="きみはどう思う？", awaiting_reply=True,
            updated_situation=scenario.initial_situation,
        ))
        session = pooh.NarrativeSession(agent, "model", "program", scenario)
        session.start()
        with patch.object(pooh, "append_log"):
            output = session.submit("うーん、わからないな")

        self.assertEqual(output.bot_response, pooh.HONEY_PROPOSAL_FALLBACK_RESPONSE)
        self.assertFalse(session.awaiting_reply)

    def test_narrative_relay_publisher_does_not_raise_when_relay_is_unreachable(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        publisher = pooh.NarrativeRelayPublisher("127.0.0.1", 1, scenario.key)
        with patch(
            "narrative_relay.socket.create_connection",
            side_effect=OSError("unreachable"),
        ), patch("narrative_relay.print"):
            publisher.publish(
                bot_response="test",
                narrative_actions=[],
                scene_id="none",
                source="participant",
            )

    def test_narrative_relay_receives_speech_input_json(self):
        class FakeSocket:
            def recv(self, _size):
                return '{"type":"user_input","text":"青い風船がいいな"}\n'.encode()

        publisher = pooh.NarrativeRelayPublisher("127.0.0.1", 8765, "eeyore_birthday")
        publisher._socket = FakeSocket()
        with patch(
            "narrative_relay.select.select",
            return_value=([publisher._socket], [], []),
        ):
            self.assertEqual(publisher.receive_user_input(), "青い風船がいいな")

    def test_narrative_relay_receive_poll_allows_timed_event_checks(self):
        publisher = pooh.NarrativeRelayPublisher("127.0.0.1", 8765, "eeyore_birthday")
        publisher._socket = object()
        with patch("narrative_relay.select.select", return_value=([], [], [])):
            self.assertIsNone(publisher.receive_user_input())

    def test_initial_state_has_required_blue_balloon_and_context(self):
        state = pooh.INITIAL_SITUATION
        self.assertEqual(state.place, "森の空き地")
        for prop in ("青い風船", "テーブル", "蜂蜜壺"):
            self.assertIn(prop, state.props)
        self.assertIn("一緒につくる仲間", state.relationship)

    def test_bot_response_field_forbids_direct_technical_terms(self):
        description = pooh.GeneratePoohResponse.bot_response.kwargs["desc"]
        self.assertIn("技術語を直接説明せず", description)
        self.assertIn("聞き違い候補", description)

    def test_all_examples_include_declared_outputs(self):
        for item in pooh.TRAINSET:
            for field in (
                "interaction_mode",
                "current_scene",
                "selected_mishearing",
                "bot_response",
            ):
                self.assertTrue(getattr(item, field, "").strip())
            self.assertIsInstance(item.narrative_actions, list)
            self.assertIsInstance(item.current_situation, NarrativeSituation)
            self.assertIsInstance(item.situation_update, SituationUpdate)
            self.assertIsInstance(item.updated_situation, NarrativeSituation)

    def test_state_update_changes_place_purpose_and_removes_character(self):
        current = NarrativeSituation(
            place="森の空き地",
            purpose="お茶会をする",
            characters=["プー", "参加者", "イーヨー"],
            props=["蜂蜜壺"],
            events=["お茶会が始まった"],
            relationship="お茶会の仲間",
            unresolved=["次に何をするか"],
        )
        updated = apply_situation_update(
            current,
            SituationUpdate(
                place="プーの家",
                purpose="お茶会を片づける",
                remove_characters=["イーヨー"],
            ),
        )
        self.assertEqual(updated.place, "プーの家")
        self.assertEqual(updated.purpose, "お茶会を片づける")
        self.assertEqual(updated.characters, ["プー", "参加者"])
        self.assertEqual(updated.props, ["蜂蜜壺"])
        self.assertEqual(updated.events, ["お茶会が始まった"])
        self.assertEqual(current.place, "森の空き地")
        self.assertIn("イーヨー", current.characters)

    def test_known_technical_terms_handle_long_vowel_variation(self):
        self.assertEqual(
            pooh.find_known_technical_terms("モータが入っているの？"),
            ["モーター"],
        )
        self.assertEqual(pooh.find_known_technical_terms("ネジも入ってる"), ["ねじ"])
        self.assertEqual(pooh.find_known_technical_terms("こんにちは"), [])

    def test_pipeline_datasets_use_separate_schemas(self):
        self.assertEqual(len(pooh.MODE_EXAMPLES), len(pooh.TRAINSET))
        self.assertEqual(len(pooh.RESPONSE_EXAMPLES), len(pooh.TRAINSET))
        self.assertEqual(len(pooh.MISHEARING_EXAMPLES), 3)
        self.assertEqual(
            pooh.MODE_EXAMPLES[0].inputs,
            ("current_situation", "user_action", "history"),
        )
        self.assertIn("mishearing_candidates", pooh.RESPONSE_EXAMPLES[0].inputs)
        self.assertIn("world_event", pooh.RESPONSE_EXAMPLES[0].inputs)
        self.assertIn("previous_bot_response", pooh.RESPONSE_EXAMPLES[0].inputs)
        robot = next(item for item in pooh.MODE_EXAMPLES if "ロボット" in item.user_action)
        self.assertEqual(robot.technical_terms, ["ロボット"])
        for item in pooh.MISHEARING_EXAMPLES:
            self.assertTrue(item.technical_terms)
            self.assertTrue(item.candidates)

    def test_interpret_stage_reads_the_final_line_and_owns_decisions(self):
        calls = {}

        def factory(signature):
            name = signature.__name__

            def predictor(**kwargs):
                calls[name] = kwargs
                if name == "AnalyzeInteraction":
                    return SimpleNamespace(
                        interaction_mode="narrative", current_scene="6", technical_terms=[],
                    )
                if name == "GeneratePoohResponse":
                    return SimpleNamespace(
                        selected_mishearing="none", situation_update=SituationUpdate(),
                        bot_response="青にしよう！",
                    )
                return SimpleNamespace(
                    narrative_actions=["not_give_empty_jar"],
                    settled_details=[SettledDetail(topic=GIFT_UNRESOLVED, value="青い風船")],
                    question_kind="deepen",
                )
            return predictor

        agent = pooh.PoohNarrativeAgent(factory)
        result = agent.forward(
            current_situation=pooh.get_scenario("eeyore_birthday").initial_situation,
            user_action="青い風船にしよう", history="",
        )

        self.assertEqual(calls["InterpretTurn"]["bot_response"], "青にしよう！")
        self.assertEqual(result.narrative_actions, ["not_give_empty_jar"])
        self.assertEqual(result.settled_details[0].value, "青い風船")
        self.assertEqual(result.question_kind, "deepen")
        self.assertTrue(result.awaiting_reply)

    def test_interpret_examples_take_the_gold_line_as_input(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        self.assertEqual(len(scenario.interpret_examples), len(scenario.trainset))
        example = scenario.interpret_examples[0]
        self.assertIn("bot_response", example.inputs)
        self.assertEqual(example.question_kind, "none")
        self.assertFalse(hasattr(scenario.response_examples[0], "narrative_actions"))

    def test_language_models_time_out_and_retry_once(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), \
                patch.object(pooh.dspy, "LM") as lm, patch.object(pooh.dspy, "configure"):
            pooh.configure_models()

        self.assertEqual(lm.call_count, 2)
        for call in lm.call_args_list:
            self.assertEqual(call.kwargs["timeout"], pooh.LM_TIMEOUT_SECONDS)
            self.assertEqual(call.kwargs["num_retries"], 1)

    def test_examples_cover_all_interaction_modes(self):
        self.assertEqual(
            {item.interaction_mode for item in pooh.TRAINSET},
            {"narrative", "ordinary", "meta", "exit"},
        )

    def test_compound_technical_question_is_meta(self):
        matches = [
            item for item in pooh.TRAINSET
            if item.user_action == "ロボットなのに食事ができるの？"
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].interaction_mode, "meta")
        self.assertEqual(matches[0].selected_mishearing, "ロバ")
        self.assertNotIn("ロボット", matches[0].bot_response)

    def test_meta_followup_example_keeps_meta_mode(self):
        matches = [item for item in pooh.TRAINSET if item.user_action == "サーボのことだよ"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].interaction_mode, "meta")
        self.assertIn("応答モード: meta", matches[0].history)
        self.assertNotIn("サーボ", matches[0].bot_response)

    def test_history_retains_mode_but_not_updated_state(self):
        turn = pooh.Turn("行為", "ordinary", "応答", "状態")
        history = pooh.format_history([turn])
        for value in ("行為", "ordinary", "応答"):
            self.assertIn(value, history)
        self.assertNotIn("状態", history)

    def test_mode_mismatch_is_a_hard_gate(self):
        def unexpected(**kwargs):
            self.fail("semantic evaluator called after mode mismatch")
        metric = pooh.make_metric(unexpected, unexpected, object())
        gold = SimpleNamespace(
            current_situation=pooh.INITIAL_SITUATION, user_action="ロボット？", history="",
            interaction_mode="meta", selected_mishearing="ロバ",
            updated_situation=pooh.INITIAL_SITUATION, bot_response="ロバ？",
        )
        pred = SimpleNamespace(
            interaction_mode="ordinary", selected_mishearing="none",
            updated_situation=pooh.INITIAL_SITUATION, bot_response="こんにちは。",
        )
        self.assertEqual(metric(gold, pred), 0.0)

    def test_settled_topic_mismatch_is_a_hard_gate(self):
        def unexpected(**kwargs):
            self.fail("semantic evaluator called after settled-topic mismatch")
        metric = pooh.make_metric(unexpected, unexpected, object())
        gold = SimpleNamespace(
            current_situation=pooh.INITIAL_SITUATION, user_action="青にしよう", history="",
            interaction_mode="narrative", selected_mishearing="none",
            updated_situation=pooh.INITIAL_SITUATION, bot_response="青にしよう！",
            narrative_actions=[],
            settled_details=[SettledDetail(topic=BALLOON_COLOR_UNRESOLVED, value="青")],
        )
        pred = SimpleNamespace(
            interaction_mode="narrative", selected_mishearing="none",
            updated_situation=pooh.INITIAL_SITUATION, bot_response="何色がいいかな？",
            narrative_actions=[], settled_details=[],
        )
        self.assertEqual(metric(gold, pred), 0.0)

    def test_asking_for_new_ideas_is_a_hard_gate_but_deepening_is_judged(self):
        judged = []

        def judge(**kwargs):
            judged.append(kwargs)
            return SimpleNamespace(**{
                name: 5 for name in (
                    "mode_accuracy", "response_fit", "narrative_coherence",
                    "participant_agency", "pooh_agency", "conversational_progress",
                    "state_quality",
                )
            })
        metric = pooh.make_metric(judge, judge, object())
        gold = SimpleNamespace(
            current_situation=pooh.INITIAL_SITUATION, user_action="風船は？", history="",
            interaction_mode="narrative", selected_mishearing="none",
            updated_situation=pooh.INITIAL_SITUATION, bot_response="風船、いいね！",
            narrative_actions=[], settled_details=[], question_kind="none",
        )

        def pred(kind):
            return SimpleNamespace(
                interaction_mode="narrative", selected_mishearing="none",
                updated_situation=pooh.INITIAL_SITUATION, bot_response="風船、いいね！",
                narrative_actions=[], settled_details=[], question_kind=kind,
            )
        self.assertEqual(metric(gold, pred("new_idea")), 0.0)
        self.assertEqual(metric(gold, pred("delegate")), 0.0)
        self.assertEqual(judged, [])
        metric(gold, pred("deepen"))
        self.assertTrue(judged)

    def test_example_ending_in_a_question_must_name_its_kind(self):
        with self.assertRaises(ValueError):
            pooh_examples.example(
                current_situation=pooh.INITIAL_SITUATION, user_action="風船は？",
                history="", interaction_mode="narrative", bot_response="何色にする？",
            )
        labeled = pooh_examples.example(
            current_situation=pooh.INITIAL_SITUATION, user_action="風船は？",
            history="", interaction_mode="narrative", bot_response="何色にする？",
            question_kind="deepen",
        )
        self.assertEqual(labeled.question_kind, "deepen")

    def test_response_invariants_prevent_repeated_question_on_hesitation(self):
        response = pooh.enforce_response_invariants(
            user_action="うーん，どうしよう",
            history="",
            bot_response="どうしようかな。きみはどう思う？",
        )
        self.assertIn("ハチミツの入った壺", response)
        self.assertNotIn("どう思う", response)

    def test_response_invariants_still_apply_while_gift_is_undecided(self):
        response = pooh.enforce_response_invariants(
            user_action="うーん，どうしよう",
            history="",
            bot_response="どうしようかな。きみはどう思う？",
            gift_status="undecided",
        )
        self.assertIn("ハチミツの入った壺", response)

    def test_response_invariants_do_not_reintroduce_a_resolved_gift(self):
        # "どうしよう" here is sympathy for the honey being gone, not
        # hesitation about which gift to propose; the jar has already been
        # decided (and eaten), so re-injecting the original pitch would
        # contradict the story.
        response = pooh.enforce_response_invariants(
            user_action="え，どうしよう，こまったね",
            history="",
            bot_response="困ったなあ。何かいいアイデアはあるかな？",
            gift_status="committed",
        )
        self.assertNotIn("ハチミツの入った壺を贈るのがいいと思うな", response)
        self.assertEqual(response, "困ったなあ。何かいいアイデアはあるかな？")

    def test_response_invariants_do_not_introduce_unmentioned_balloon(self):
        response = pooh.enforce_response_invariants(
            user_action="いいね",
            history="プーの応答: ハチミツの入った壺を贈ろう。",
            bot_response="風船も考えてくれたし、素敵なプレゼントだね。",
        )
        self.assertNotIn("風船", response)

    def test_warn_if_response_repeated_flags_exact_duplicate(self):
        with patch("builtins.print") as mock_print:
            pooh.warn_if_response_repeated("うん、そうだね。", "うん、そうだね。")
        mock_print.assert_called_once()
        self.assertEqual(mock_print.call_args.kwargs.get("file"), sys.stderr)

    def test_warn_if_response_repeated_ignores_first_turn_and_new_text(self):
        with patch("builtins.print") as mock_print:
            pooh.warn_if_response_repeated("うん、そうだね。", None)
            pooh.warn_if_response_repeated("うん、そうだね。", "ちがう文。")
        mock_print.assert_not_called()

    def test_format_situation_diff_shows_only_changes(self):
        before = pooh.INITIAL_SITUATION
        after = before.model_copy(
            update={
                "events": [*before.events, "プーが決めた"],
                "unresolved": [],
            },
            deep=True,
        )
        diff = pooh.format_situation_diff(before, after)
        self.assertIn("重要な出来事: 追加", diff)
        self.assertIn("プーが決めた", diff)
        for item in before.unresolved:
            self.assertIn(f"未解決・未確定: 削除", diff)
            self.assertIn(item, diff)

    def test_format_situation_diff_reports_no_change(self):
        self.assertEqual(
            pooh.format_situation_diff(pooh.INITIAL_SITUATION, pooh.INITIAL_SITUATION),
            "(変化なし)",
        )

    def test_format_situation_diff_shows_full_state_without_previous(self):
        diff = pooh.format_situation_diff(None, pooh.INITIAL_SITUATION)
        self.assertEqual(diff, str(pooh.INITIAL_SITUATION))

    def test_chat_warns_but_does_not_replace_repeated_response(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        agent = Mock(return_value=SimpleNamespace(
            interaction_mode="narrative",
            current_scene="2",
            narrative_actions=[],
            bot_response="うん、そうだね。",
            updated_situation=scenario.initial_situation,
        ))
        answers = iter(["いいね", "そうだね", "exit"])
        with patch("builtins.print") as mock_print, patch.object(pooh, "append_log"):
            pooh.run_chat(
                agent,
                "test-model",
                "test-program",
                scenario,
                input_fn=lambda _prompt: next(answers),
            )
        warnings = [
            call for call in mock_print.call_args_list
            if call.kwargs.get("file") is sys.stderr
        ]
        self.assertEqual(len(warnings), 1)
        self.assertIn("うん、そうだね。", warnings[0].args[0])

    def test_narrative_action_mismatch_is_a_hard_gate(self):
        def unexpected(**kwargs):
            self.fail("semantic evaluator called after narrative action mismatch")
        metric = pooh.make_metric(unexpected, unexpected, object())
        gold = SimpleNamespace(
            current_situation=pooh.INITIAL_SITUATION,
            user_action="それにしよう",
            history="",
            interaction_mode="narrative",
            current_scene="none",
            narrative_actions=["commit_honey_jar_gift"],
            bot_response="うん、それにしよう。",
        )
        pred = SimpleNamespace(
            interaction_mode="narrative",
            current_scene="none",
            narrative_actions=[],
            bot_response="うん、それにしよう。",
        )
        self.assertEqual(metric(gold, pred), 0.0)

    def test_exact_repeat_of_previous_response_is_a_hard_gate(self):
        def unexpected(**kwargs):
            self.fail("judge called after an exact repeat of the previous response")
        metric = pooh.make_metric(unexpected, unexpected, object())
        gold = SimpleNamespace(
            current_situation=pooh.INITIAL_SITUATION,
            user_action="そうだね",
            history="",
            interaction_mode="narrative",
            current_scene="none",
            narrative_actions=[],
            previous_bot_response="うん、それにしよう。",
            bot_response="別の応答。",
        )
        pred = SimpleNamespace(
            interaction_mode="narrative",
            current_scene="none",
            narrative_actions=[],
            bot_response="うん、それにしよう。",
        )
        self.assertEqual(metric(gold, pred), 0.0)

    def test_judge_receives_previous_bot_response_for_semantic_evaluation(self):
        scores = SimpleNamespace(
            mode_accuracy=5, response_fit=5, narrative_coherence=5,
            participant_agency=5, pooh_agency=5, state_quality=5,
            conversational_progress=5,
        )
        judge = Mock(return_value=scores)
        metric = pooh.make_metric(judge, Mock(), object())
        gold = SimpleNamespace(
            current_situation=pooh.INITIAL_SITUATION,
            user_action="そうだね",
            history="",
            interaction_mode="narrative",
            current_scene="none",
            narrative_actions=[],
            previous_bot_response="うん、それにしよう。",
            bot_response="別の応答。",
        )
        pred = SimpleNamespace(
            interaction_mode="narrative",
            current_scene="none",
            narrative_actions=[],
            bot_response="似ているけれど一致はしない応答。",
        )
        self.assertEqual(metric(gold, pred), 1.0)
        self.assertEqual(
            judge.call_args.kwargs["previous_bot_response"],
            gold.previous_bot_response,
        )

    def test_meta_policy_is_a_hard_gate(self):
        scores = SimpleNamespace(
            mode_accuracy=5,
            response_fit=5,
            narrative_coherence=5,
            participant_agency=5,
            pooh_agency=5,
            state_quality=5,
            conversational_progress=5,
        )
        judge = lambda **kwargs: scores
        captured = {}
        def violating(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(policy_compliance=3)
        metric = pooh.make_metric(judge, violating, object())
        gold = SimpleNamespace(
            current_situation=pooh.INITIAL_SITUATION,
            user_action="ロボット？",
            history="",
            interaction_mode="meta",
            updated_situation=pooh.INITIAL_SITUATION,
            bot_response="ロバ？",
        )
        pred = SimpleNamespace(
            interaction_mode="meta",
            updated_situation=pooh.INITIAL_SITUATION,
            bot_response="ぼくはロボットではないよ。",
        )
        self.assertEqual(metric(gold, pred), 0.0)
        self.assertNotIn("candidate_situation", captured)

    def test_meta_examples_do_not_repeat_technical_term_in_response(self):
        for item, mode_example in zip(pooh.TRAINSET, pooh.MODE_EXAMPLES):
            if item.interaction_mode != "meta":
                continue
            self.assertFalse(
                pooh.contains_technical_term(
                    item.bot_response,
                    mode_example.technical_terms,
                ),
                msg=f"技術語を含む応答です: {item.user_action}",
            )

    def test_repetition_quality_is_a_hard_gate(self):
        scores = SimpleNamespace(
            mode_accuracy=5, response_fit=5, narrative_coherence=5,
            participant_agency=5, pooh_agency=5, state_quality=5,
            conversational_progress=3,
        )
        judge = Mock(return_value=scores)
        metric = pooh.make_metric(judge, Mock(), object())
        gold = SimpleNamespace(
            current_situation=pooh.INITIAL_SITUATION,
            user_action="そっかあ、いいね",
            history="プーの応答: 大丈夫だよ、みんなで分け合えばいいよ。",
            interaction_mode="narrative",
            bot_response="うん。ふたをしておこう。",
        )
        pred = SimpleNamespace(
            interaction_mode="narrative",
            bot_response="大丈夫だよ、みんなで分け合えばいいよ。",
        )
        self.assertEqual(metric(gold, pred), 0.0)
        self.assertEqual(judge.call_args.kwargs["history"], gold.history)
        # Explicit confirmation/repetition requests can receive a high score.
        gold.user_action = "もう一度言って？"
        scores.conversational_progress = 5
        self.assertEqual(metric(gold, pred), 1.0)

    def test_non_meta_does_not_call_meta_evaluator(self):
        scores = SimpleNamespace(
            mode_accuracy=5,
            response_fit=5,
            narrative_coherence=5,
            participant_agency=5,
            pooh_agency=5,
            state_quality=5,
            conversational_progress=5,
        )
        judge = lambda **kwargs: scores
        def unexpected(**kwargs):
            self.fail("meta evaluator called for non-meta example")
        metric = pooh.make_metric(judge, unexpected, object())
        gold = SimpleNamespace(
            current_situation=pooh.INITIAL_SITUATION,
            user_action="こんにちは",
            history="",
            interaction_mode="ordinary",
            updated_situation=pooh.INITIAL_SITUATION,
            bot_response="こんにちは。",
        )
        pred = SimpleNamespace(
            interaction_mode="ordinary",
            updated_situation=pooh.INITIAL_SITUATION,
            bot_response="こんにちは。",
        )
        self.assertEqual(metric(gold, pred), 1.0)

    def test_uncertain_echo_is_short_and_changes_the_term(self):
        expected = {
            "ロボット": "ロボ",
            "モーター": "モタ",
            "サーボ": "サボ",
        }
        for technical_term, expected_echo in expected.items():
            echo = make_uncertain_echo(technical_term)
            self.assertEqual(echo, expected_echo)
            self.assertLessEqual(len(echo), 3)
            self.assertNotEqual(
                normalize_term(echo),
                normalize_term(technical_term),
            )
            candidate = uncertain_candidate_for(technical_term)
            allowed = {
                template.format(echo=echo)
                for template in UNCERTAIN_RESPONSE_TEMPLATES
            }
            self.assertIn(candidate.possible_response, allowed)

    def test_prior_technical_mentions_count_user_input_only(self):
        history = (
            "Turn 1\n参加者の生入力: ロボットなの？\n応答モード: meta\n"
            "プーの応答: ロバ？\n"
            "Turn 2\n参加者の生入力: ロボットのことだよ\n応答モード: meta"
        )
        self.assertEqual(
            pooh.count_prior_technical_mentions(history, ["ロボット"]),
            2,
        )
        self.assertEqual(
            pooh.count_prior_technical_mentions(history, ["モーター"]),
            0,
        )

    def test_reviewed_mishearing_dictionary(self):
        expressions = [case.technical_expression for case in MISHEARING_CASES]
        self.assertEqual(len(expressions), len(set(expressions)))

        for case in MISHEARING_CASES:
            self.assertIn(
                case.technical_expression,
                pooh.find_known_technical_terms(case.technical_expression),
            )
            candidates, unknown = pooh.known_candidates_for(
                [case.technical_expression]
            )
            self.assertEqual(
                [candidate.heard_as for candidate in candidates],
                [case.misheard_word],
            )
            self.assertEqual(unknown, [])

    def test_stage_demos_fill_budget_after_bootstrapping(self):
        augmented = object()
        labeled = [object(), object(), object()]
        predictor = SimpleNamespace(demos=[augmented])
        module = SimpleNamespace(predictors=lambda: [predictor])

        pooh.merge_module_demos(module, labeled)

        self.assertIs(predictor.demos[0], augmented)
        self.assertEqual(predictor.demos[1:], labeled)

    def test_merge_module_demos_caps_total_at_fixed_budget(self):
        bootstrapped = [object() for _ in range(pooh.MAX_BOOTSTRAPPED_DEMOS)]
        labeled = [object() for _ in range(pooh.MAX_TOTAL_DEMOS_PER_STAGE * 3)]
        predictor = SimpleNamespace(demos=list(bootstrapped))
        module = SimpleNamespace(predictors=lambda: [predictor])

        pooh.merge_module_demos(module, labeled)

        self.assertLessEqual(len(predictor.demos), pooh.MAX_TOTAL_DEMOS_PER_STAGE)

    def test_merge_module_demos_keeps_earliest_and_most_recent_labeled_examples(self):
        labeled = [f"example-{i}" for i in range(pooh.MAX_TOTAL_DEMOS_PER_STAGE * 3)]
        predictor = SimpleNamespace(demos=[])
        module = SimpleNamespace(predictors=lambda: [predictor])

        pooh.merge_module_demos(module, labeled)

        self.assertIn(labeled[0], predictor.demos)
        self.assertIn(labeled[-1], predictor.demos)
        self.assertLessEqual(len(predictor.demos), pooh.MAX_TOTAL_DEMOS_PER_STAGE)

    def test_select_labeled_demos_stays_diverse_when_list_is_grouped_by_trait(self):
        # A source file may be sorted by some trait (e.g. scene) for human
        # readability; demo selection must not silently starve whichever
        # trait values end up outside the old head/tail windows.
        labeled = (
            [SimpleNamespace(group="a") for _ in range(10)]
            + [SimpleNamespace(group="b") for _ in range(10)]
            + [SimpleNamespace(group="c") for _ in range(10)]
            + [SimpleNamespace(group="d") for _ in range(10)]
        )

        selected = pooh.select_labeled_demos(labeled, 8)

        groups = {item.group for item in selected}
        self.assertEqual(groups, {"a", "b", "c", "d"})

    def test_select_labeled_demos_from_real_eeyore_mode_examples_covers_most_scenes(self):
        scenario = pooh.get_scenario("eeyore_birthday")

        selected = pooh.select_labeled_demos(scenario.mode_examples, 8)

        scenes = {item.current_scene for item in selected}
        self.assertGreater(len(scenes), 2)

    def test_growing_response_examples_do_not_exceed_demo_cap_after_merge(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        predictor = SimpleNamespace(demos=[])
        module = SimpleNamespace(predictors=lambda: [predictor])

        pooh.merge_module_demos(module, scenario.response_examples)

        self.assertLessEqual(len(predictor.demos), pooh.MAX_TOTAL_DEMOS_PER_STAGE)

    def test_cache_changes_with_mishearing_examples(self):
        before = pooh.cache_hash("model", "judge")
        example = pooh.MISHEARING_EXAMPLES.pop()
        try:
            self.assertNotEqual(before, pooh.cache_hash("model", "judge"))
        finally:
            pooh.MISHEARING_EXAMPLES.append(example)

    def test_cache_changes_with_model_or_judge(self):
        self.assertNotEqual(pooh.cache_hash("model-a", "judge"), pooh.cache_hash("model-b", "judge"))
        self.assertNotEqual(pooh.cache_hash("model", "judge-a"), pooh.cache_hash("model", "judge-b"))

    def test_timer_delay_is_runtime_configuration_not_cache_input(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        changed_delay = replace(
            scenario,
            gift_decision_delay_seconds=scenario.gift_decision_delay_seconds + 10,
            honey_tasting_delay_seconds=scenario.honey_tasting_delay_seconds + 5,
            honey_eating_delay_seconds=scenario.honey_eating_delay_seconds + 5,
            event_quiet_seconds=scenario.event_quiet_seconds + 5,
        )
        self.assertEqual(
            pooh.cache_hash("model", "judge", scenario),
            pooh.cache_hash("model", "judge", changed_delay),
        )

    def test_scene_ids_are_display_only_and_validated_by_scenario(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        self.assertEqual(
            pooh.normalize_scene_id("3", scenario),
            "3",
        )
        self.assertEqual(pooh.normalize_scene_id("made_up_scene", scenario), "none")

    def test_honey_event_fires_once_without_resetting_on_same_decision(self):
        now = [100.0]
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: now[0])
        controller.observe_actions(["commit_honey_jar_gift"])
        self.assertEqual(controller.seconds_until_due(), 30.0)

        now[0] = 105.0
        controller.observe_actions(["commit_honey_jar_gift"])
        self.assertEqual(controller.seconds_until_due(), 25.0)
        self.assertIsNone(controller.pop_due_event())

        now[0] = 130.0
        taste_event = controller.pop_due_event()
        self.assertIsNotNone(taste_event)
        self.assertEqual(taste_event.event_id, "pooh_tastes_honey")
        self.assertEqual(controller.state.honey_status, "full")
        self.assertEqual(controller.seconds_until_due(), 10.0)

        now[0] = 140.0
        event = controller.pop_due_event()
        self.assertIsNotNone(event)
        self.assertEqual(event.event_id, "pooh_ate_honey")
        self.assertEqual(event.fallback_response, HONEY_EATEN_RESPONSE)
        self.assertEqual(controller.state.honey_status, "empty")
        self.assertIsNone(controller.pop_due_event())

        updated = controller.synchronize_situation(pooh.get_scenario("eeyore_birthday").initial_situation)
        self.assertNotIn("蜂蜜壺", updated.props)
        self.assertIn("空になった蜂蜜壺", updated.props)
        self.assertIn(HONEY_TASTED_EVENT, updated.events)

    def test_required_events_commit_then_taste_then_eat_after_inactivity(self):
        now = [0.0]
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: now[0])
        now[0] = 30.0
        commit_event = controller.pop_due_event()
        self.assertIsNotNone(commit_event)
        self.assertEqual(commit_event.event_id, "honey_gift_committed")
        self.assertEqual(commit_event.scene_id, "1c")
        self.assertEqual(controller.state.gift_status, "committed")

        now[0] = 59.9
        self.assertIsNone(controller.pop_due_event())
        now[0] = 60.0
        taste_event = controller.pop_due_event()
        self.assertIsNotNone(taste_event)
        self.assertEqual(taste_event.event_id, "pooh_tastes_honey")
        self.assertEqual(taste_event.scene_id, "2")

        now[0] = 69.9
        self.assertIsNone(controller.pop_due_event())
        now[0] = 70.0
        eat_event = controller.pop_due_event()
        self.assertIsNotNone(eat_event)
        self.assertEqual(eat_event.event_id, "pooh_ate_honey")
        self.assertEqual(eat_event.scene_id, "3")

    def test_scene_opening_event_waits_for_quiet_after_deadline(self):
        now = [0.0]
        controller = HoneyGiftEventController(
            30.0, 30.0, 10.0, clock=lambda: now[0], quiet_seconds=12.0,
        )
        controller.observe_actions(["commit_honey_jar_gift"])

        # Pooh just asked something; the deadline passes mid-exchange.
        now[0] = 25.0
        controller.observe_activity()
        now[0] = 31.0
        self.assertIsNone(controller.pop_due_event())
        self.assertEqual(controller.seconds_until_due(), 6.0)

        now[0] = 37.0
        event = controller.pop_due_event()
        self.assertEqual(event.event_id, "pooh_tastes_honey")

    def test_overdue_event_follows_the_answer_without_waiting_for_quiet(self):
        now = [0.0]
        controller = HoneyGiftEventController(
            30.0, 30.0, 10.0, clock=lambda: now[0], quiet_seconds=12.0,
        )
        controller.observe_actions(["commit_honey_jar_gift"])

        now[0] = 20.0
        controller.observe_activity()
        self.assertIsNone(controller.pop_due_event(follow_up=True))

        now[0] = 31.0
        controller.observe_activity()
        event = controller.pop_due_event(follow_up=True)
        self.assertEqual(event.event_id, "pooh_tastes_honey")
        self.assertEqual(event.follow_up_response, HONEY_TASTED_FOLLOW_UP_RESPONSE)

    def test_gift_decision_fires_even_when_participant_keeps_talking(self):
        now = [0.0]
        controller = HoneyGiftEventController(
            30.0, 30.0, 10.0, clock=lambda: now[0], quiet_seconds=12.0,
        )
        seen = []
        for _ in range(4):
            now[0] += 10.0
            controller.observe_activity()
            self.assertIsNone(controller.pop_due_event())
            event = controller.pop_due_event(follow_up=True)
            if event is not None:
                seen.append((now[0], event.event_id))
        self.assertEqual(seen, [(30.0, "honey_gift_committed")])

    def test_pooh_proposing_the_honey_jar_decides_it_without_another_line(self):
        now = [0.0]
        unproposed = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: now[0])
        proposed = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: now[0])
        proposed.observe_activity(8.0)  # the proposal line being spoken
        proposed.observe_actions(["propose_honey_jar_gift"])

        self.assertEqual(proposed.state.gift_status, "committed")
        self.assertEqual(proposed.state.settled_details[POOH_GIFT_TOPIC], "ハチミツの入った壺")
        # Tasting counts from the end of the proposal line.
        self.assertEqual(proposed._deadlines["pooh_tastes_honey"], 38.0)
        now[0] = 30.0
        self.assertEqual(unproposed.pop_due_event().fallback_response,
                         HONEY_GIFT_COMMITTED_RESPONSE)
        self.assertIsNone(proposed.pop_due_event())

    def test_pooh_gift_does_not_overwrite_the_participant_gift(self):
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: 0.0)
        controller.observe_settled_details([SettledDetail(topic=GIFT_UNRESOLVED, value="ケーキ")])
        controller.observe_actions(["propose_honey_jar_gift"])
        # The model cannot rewrite Pooh's own gift either.
        controller.observe_settled_details(
            [SettledDetail(topic=POOH_GIFT_TOPIC, value="風船")]
        )

        updated = controller.synchronize_situation(
            pooh.get_scenario("eeyore_birthday").initial_situation
        )
        self.assertIn("イーヨーに何をあげるか：ケーキ", updated.decided)
        self.assertIn("プーがあげるもの：ハチミツの入った壺", updated.decided)

    def test_runtime_honey_proposal_fallback_is_recorded_as_proposal(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: 0.0)
        agent = Mock(return_value=SimpleNamespace(
            interaction_mode="narrative",
            current_scene="1a",
            narrative_actions=[],
            bot_response="きみはどう思う？",
            updated_situation=scenario.initial_situation,
        ))
        session = pooh.NarrativeSession(
            agent, "model", "program", scenario, event_controller=controller,
        )
        session.start()
        with patch.object(pooh, "append_log"):
            output = session.submit("うーん、わからないな")

        self.assertEqual(output.bot_response, pooh.HONEY_PROPOSAL_FALLBACK_RESPONSE)
        self.assertIn("propose_honey_jar_gift", output.narrative_actions)
        # The guard voiced Pooh's own proposal, which decides his gift.
        self.assertEqual(controller.state.gift_status, "committed")

    def _controller_with_empty_jar(self, now):
        controller = HoneyGiftEventController(
            30.0, 30.0, 10.0, clock=lambda: now[0],
            quiet_seconds=12.0, idle_close_seconds=30.0,
        )
        controller.observe_actions(["commit_honey_jar_gift"])
        start = now[0]
        now[0] = start + 42.0
        self.assertEqual(controller.pop_due_event().event_id, "pooh_tastes_honey")
        now[0] = start + 52.0
        self.assertEqual(controller.pop_due_event().event_id, "pooh_ate_honey")
        controller.observe_activity()
        return controller

    def test_wrap_up_follows_the_answer_that_resolves_the_empty_jar(self):
        now = [100.0]
        controller = self._controller_with_empty_jar(now)
        controller.observe_activity()
        self.assertIsNone(controller.pop_due_event(follow_up=True))

        controller.observe_actions(["give_empty_jar"])
        controller.observe_activity()
        event = controller.pop_due_event(follow_up=True)
        self.assertEqual(event.event_id, "story_wrap_up")
        self.assertEqual(event.fallback_response, STORY_WRAP_UP_RESPONSE)
        self.assertFalse(event.ends_session)
        updated = controller.synchronize_situation(
            pooh.get_scenario("eeyore_birthday").initial_situation
        )
        self.assertIn(STORY_WRAP_UP_EVENT, updated.events)

    def test_idle_close_needs_silence_and_never_follows_an_answer(self):
        now = [100.0]
        controller = self._controller_with_empty_jar(now)
        controller.observe_actions(["give_empty_jar"])
        controller.pop_due_event(follow_up=True)
        controller.observe_activity()

        # The participant keeps talking: the story continues.
        for _ in range(3):
            now[0] += 25.0
            controller.observe_activity()
            self.assertIsNone(controller.pop_due_event(follow_up=True))
            self.assertIsNone(controller.pop_due_event())

        self.assertEqual(controller.seconds_until_due(), 30.0)
        now[0] += 30.0
        event = controller.pop_due_event()
        self.assertEqual(event.event_id, "idle_close_after_wrap_up")
        self.assertTrue(event.ends_session)

    def test_session_idle_close_plays_the_ending_line(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        now = [100.0]
        controller = self._controller_with_empty_jar(now)
        controller.observe_actions(["give_empty_jar"])
        controller.pop_due_event(follow_up=True)
        publisher = Mock()
        session = pooh.NarrativeSession(
            Mock(), "model", "program", scenario,
            event_controller=controller, ros_publisher=publisher,
        )
        session.start()
        now[0] += 30.0
        output = session.poll()

        self.assertTrue(session.ended)
        self.assertEqual(output.source, "session_close")
        self.assertEqual(output.bot_response, scenario.ending_line)
        self.assertEqual(output.performance_cue, "ending")
        self.assertIsNone(session.close())
        self.assertEqual(publisher.publish.call_count, 2)

    def test_story_clock_starts_after_the_opening_is_spoken(self):
        now = [0.0]
        controller = HoneyGiftEventController(
            30.0, 30.0, 10.0, clock=lambda: now[0], quiet_seconds=12.0,
        )
        controller.begin(27.0)

        now[0] = 30.0
        self.assertIsNone(controller.pop_due_event())
        self.assertEqual(controller.seconds_until_due(), 27.0)
        now[0] = 57.0
        self.assertEqual(controller.pop_due_event().event_id, "honey_gift_committed")

    def test_eating_waits_for_the_tasting_line_to_finish(self):
        now = [0.0]
        controller = HoneyGiftEventController(
            30.0, 30.0, 10.0, clock=lambda: now[0],
            speech_seconds=lambda text: 13.0 if text == HONEY_TASTED_RESPONSE else 0.0,
        )
        controller.observe_actions(["commit_honey_jar_gift"])
        now[0] = 30.0
        self.assertEqual(controller.pop_due_event().event_id, "pooh_tastes_honey")

        now[0] = 40.0
        self.assertIsNone(controller.pop_due_event())
        now[0] = 53.0
        self.assertEqual(controller.pop_due_event().event_id, "pooh_ate_honey")

    def test_quiet_time_counts_from_the_end_of_pooh_speech(self):
        now = [0.0]
        controller = HoneyGiftEventController(
            30.0, 30.0, 10.0, clock=lambda: now[0], quiet_seconds=12.0,
        )
        controller.observe_actions(["commit_honey_jar_gift"])
        now[0] = 25.0
        controller.observe_activity(8.0)

        now[0] = 40.0
        self.assertIsNone(controller.pop_due_event())
        now[0] = 45.0
        self.assertEqual(controller.pop_due_event().event_id, "pooh_tastes_honey")

    def test_speech_estimate_matches_measured_fixed_utterance_rate(self):
        # The eeyore opening WAV is 26.9 s long.
        self.assertAlmostEqual(
            pooh.estimate_speech_seconds(pooh.get_scenario("eeyore_birthday").opening_line),
            26.9,
            delta=3.0,
        )

    def test_empty_jar_item_stays_unresolved_until_the_action_settles_it(self):
        now = [0.0]
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: now[0])
        controller.observe_actions(["commit_honey_jar_gift"])
        now[0] = 30.0
        controller.pop_due_event()
        now[0] = 40.0
        controller.pop_due_event()
        # The model reworded the item and later dropped it without the action.
        reworded = pooh.get_scenario("eeyore_birthday").initial_situation.model_copy(
            update={"unresolved": ["イーヨーへの贈り物をどうするか"]},
        )
        dropped = reworded.model_copy(update={"unresolved": []})

        self.assertIn(EMPTY_JAR_UNRESOLVED, controller.synchronize_situation(reworded).unresolved)
        self.assertIn(EMPTY_JAR_UNRESOLVED, controller.synchronize_situation(dropped).unresolved)

        controller.observe_actions(["give_empty_jar"])
        self.assertNotIn(
            EMPTY_JAR_UNRESOLVED, controller.synchronize_situation(dropped).unresolved,
        )

    def test_open_topics_such_as_drinks_are_kept_as_decided(self):
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: 0.0)
        controller.observe_settled_details([
            SettledDetail(topic="飲み物", value="ハチミツたっぷりのアザミドリンク"),
            SettledDetail(topic="  ", value="無視される"),
        ])
        updated = controller.synchronize_situation(
            pooh.get_scenario("eeyore_birthday").initial_situation
        )
        self.assertEqual(updated.decided, ["飲み物：ハチミツたっぷりのアザミドリンク"])

    def _controller_after_honey_eaten(self, now):
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: now[0])
        controller.observe_actions(["commit_honey_jar_gift"])
        now[0] = 30.0
        controller.pop_due_event()
        now[0] = 40.0
        self.assertEqual(controller.pop_due_event().event_id, "pooh_ate_honey")
        return controller

    def test_giving_the_empty_jar_completes_the_story(self):
        now = [0.0]
        controller = self._controller_after_honey_eaten(now)
        controller.observe_actions(["give_empty_jar"])

        self.assertEqual(controller.pop_due_event(follow_up=True).event_id, "story_wrap_up")
        updated = controller.synchronize_situation(
            pooh.get_scenario("eeyore_birthday").initial_situation.model_copy(
                update={"unresolved": [EMPTY_JAR_UNRESOLVED]},
            )
        )
        self.assertNotIn(EMPTY_JAR_UNRESOLVED, updated.unresolved)
        self.assertIn("空になった壺をどうするか：あげる", updated.decided)

    def test_not_giving_the_jar_needs_another_gift_to_complete(self):
        now = [0.0]
        controller = self._controller_after_honey_eaten(now)
        controller.observe_actions(["not_give_empty_jar"])

        self.assertIsNone(controller.pop_due_event(follow_up=True))
        waiting = controller.synchronize_situation(
            pooh.get_scenario("eeyore_birthday").initial_situation.model_copy(
                update={"unresolved": []},
            )
        )
        self.assertEqual(waiting.unresolved, [GIFT_UNRESOLVED])
        self.assertEqual(waiting.decided, ["空になった壺をどうするか：あげない"])

        controller.observe_settled_details([SettledDetail(topic=GIFT_UNRESOLVED, value="風船")])
        self.assertEqual(controller.pop_due_event(follow_up=True).event_id, "story_wrap_up")
        done = controller.synchronize_situation(waiting)
        self.assertEqual(done.unresolved, [])
        self.assertEqual(
            done.decided, ["空になった壺をどうするか：あげない", "イーヨーに何をあげるか：風船"],
        )

    def test_gift_decisions_before_the_honey_is_eaten_do_not_complete_the_story(self):
        now = [0.0]
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: now[0])
        controller.observe_settled_details([SettledDetail(topic=GIFT_UNRESOLVED, value="風船")])
        controller.observe_actions(["not_give_empty_jar", "give_empty_jar"])
        self.assertIsNone(controller.state.jar_decision)
        controller.observe_actions(["commit_honey_jar_gift"])
        # Pooh's honey jar is recorded apart from the participant's choice.
        self.assertEqual(controller.state.settled_details[POOH_GIFT_TOPIC], "ハチミツの入った壺")
        self.assertEqual(controller.state.settled_details[GIFT_UNRESOLVED], "風船")
        now[0] = 30.0
        controller.pop_due_event()
        now[0] = 40.0
        controller.pop_due_event()

        self.assertNotIn(POOH_GIFT_TOPIC, controller.state.settled_details)
        self.assertIsNone(controller.pop_due_event(follow_up=True))

    def test_contradictory_jar_actions_in_one_turn_are_both_ignored(self):
        now = [0.0]
        controller = self._controller_after_honey_eaten(now)
        controller.observe_actions(["give_empty_jar", "not_give_empty_jar"])

        self.assertIsNone(controller.state.jar_decision)
        self.assertIsNone(controller.pop_due_event(follow_up=True))

    def test_anything_settled_after_the_honey_is_eaten_completes_the_story(self):
        now = [0.0]
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: now[0])
        # Decided before the honey beat: does not count.
        controller.observe_settled_details([SettledDetail(topic="ケーキ", value="アザミのケーキ")])
        controller.observe_actions(["commit_honey_jar_gift"])
        now[0] = 30.0
        controller.pop_due_event()
        now[0] = 40.0
        controller.pop_due_event()
        self.assertIsNone(controller.pop_due_event(follow_up=True))

        controller.observe_settled_details([SettledDetail(topic="飲み物", value="オレンジジュース")])
        self.assertEqual(controller.pop_due_event(follow_up=True).event_id, "story_wrap_up")
        updated = controller.synchronize_situation(
            pooh.get_scenario("eeyore_birthday").initial_situation.model_copy(
                update={"unresolved": [EMPTY_JAR_UNRESOLVED]},
            )
        )
        self.assertNotIn(EMPTY_JAR_UNRESOLVED, updated.unresolved)

    def test_silence_after_a_question_waits_longer_before_the_wrap_up(self):
        for awaiting, due_after in ((True, 30.0), (False, 12.0)):
            now = [0.0]
            controller = HoneyGiftEventController(
                30.0, 30.0, 10.0, clock=lambda: now[0],
                quiet_seconds=12.0, awaiting_quiet_seconds=30.0,
            )
            controller.observe_actions(["commit_honey_jar_gift"])
            now[0] = 42.0
            controller.pop_due_event()
            now[0] = 52.0
            controller.pop_due_event()
            now[0] = 100.0
            controller.observe_settled_details([SettledDetail(topic="飲み物", value="お茶")])
            controller.observe_activity(0.0, awaiting_reply=awaiting)

            now[0] = 100.0 + due_after - 0.1
            self.assertIsNone(controller.pop_due_event(), awaiting)
            now[0] = 100.0 + due_after
            self.assertEqual(controller.pop_due_event().event_id, "story_wrap_up", awaiting)

    def test_model_cannot_settle_the_jar_question_as_free_text(self):
        now = [0.0]
        controller = self._controller_after_honey_eaten(now)
        controller.observe_settled_details(
            [SettledDetail(topic=EMPTY_JAR_UNRESOLVED, value="空の壺にクッキーを入れて贈る")]
        )
        self.assertIsNone(controller.state.jar_decision)
        self.assertIsNone(controller.pop_due_event(follow_up=True))

    def test_settled_detail_stays_decided_and_later_value_replaces_it(self):
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: 0.0)
        situation = pooh.get_scenario("eeyore_birthday").initial_situation.model_copy(
            update={"unresolved": [BALLOON_COLOR_UNRESOLVED]},
        )
        controller.observe_settled_details([SettledDetail(topic=BALLOON_COLOR_UNRESOLVED, value="青")])
        first = controller.synchronize_situation(situation)
        self.assertEqual(first.decided, ["贈り物にする風船の色：青"])
        self.assertNotIn(BALLOON_COLOR_UNRESOLVED, first.unresolved)
        self.assertIn("【決まったこと】贈り物にする風船の色：青", str(first))

        # The model's own delta has no way to drop what Python decided.
        after_turn = apply_situation_update(first, SituationUpdate(add_events=["別の話をした"]))
        self.assertEqual(after_turn.decided, ["贈り物にする風船の色：青"])

        controller.observe_settled_details([{"topic": BALLOON_COLOR_UNRESOLVED, "value": "黄色"}])
        self.assertEqual(
            controller.synchronize_situation(after_turn).decided, ["贈り物にする風船の色：黄色"],
        )

    def test_session_records_settled_details_from_the_model(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: 0.0)
        agent = Mock(return_value=SimpleNamespace(
            interaction_mode="narrative",
            current_scene="6",
            narrative_actions=[],
            settled_details=[SettledDetail(topic=BALLOON_COLOR_UNRESOLVED, value="青")],
            bot_response="青にしよう！晴れた空みたいだもの。",
            updated_situation=scenario.initial_situation,
        ))
        session = pooh.NarrativeSession(
            agent, "model", "program", scenario, event_controller=controller,
        )
        session.start()
        with patch.object(pooh, "append_log") as log:
            output = session.submit("青がいいな")

        self.assertEqual(output.updated_situation.decided, ["贈り物にする風船の色：青"])
        self.assertEqual(
            log.call_args.args[1].settled_details,
            [{"topic": BALLOON_COLOR_UNRESOLVED, "value": "青"}],
        )

    def test_wrap_up_follows_an_answer_that_is_still_being_spoken(self):
        now = [0.0]
        controller = HoneyGiftEventController(
            30.0, 30.0, 10.0, clock=lambda: now[0], quiet_seconds=12.0,
        )
        controller.observe_actions(["commit_honey_jar_gift"])
        now[0] = 42.0
        controller.pop_due_event()
        now[0] = 52.0
        controller.pop_due_event()
        now[0] = 100.0
        controller.observe_activity(10.0)  # the answer that settles the gift
        controller.observe_actions(["not_give_empty_jar"])
        controller.observe_settled_details([SettledDetail(topic=GIFT_UNRESOLVED, value="風船")])

        self.assertIsNone(controller.pop_due_event())
        self.assertEqual(controller.pop_due_event(follow_up=True).event_id, "story_wrap_up")

    def test_participant_input_does_not_reset_eating_timer(self):
        now = [0.0]
        controller = HoneyGiftEventController(
            30.0, 30.0, 10.0, clock=lambda: now[0], quiet_seconds=12.0,
        )
        controller.observe_actions(["commit_honey_jar_gift"])
        seen_event_ids = []
        for _ in range(8):
            now[0] += 10.0
            controller.observe_activity()
            event = controller.pop_due_event(follow_up=True)
            if event is not None:
                seen_event_ids.append(event.event_id)
                if event.event_id == "pooh_ate_honey":
                    return
        self.assertIn(
            "pooh_ate_honey",
            seen_event_ids,
            "eating event was indefinitely postponed by unrelated input",
        )

    def test_synchronize_situation_clears_gift_unresolved_without_example_support(self):
        now = [0.0]
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: now[0])
        controller.observe_actions(["commit_honey_jar_gift"])
        situation = pooh.get_scenario("eeyore_birthday").initial_situation
        for item in GIFT_DECISION_UNRESOLVED:
            self.assertIn(item, situation.unresolved)

        # Simulate an Example whose own situation_update forgot to remove
        # the resolved unresolved items; the controller must still clear them.
        updated = controller.synchronize_situation(situation)
        for item in GIFT_DECISION_UNRESOLVED:
            self.assertNotIn(item, updated.unresolved)
        self.assertIn(HONEY_GIFT_COMMITTED_EVENT, updated.events)

    def test_synchronize_situation_clears_empty_jar_unresolved_without_example_support(self):
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: 0.0)
        controller.observe_actions(["commit_honey_jar_gift"])
        controller.state.honey_status = "empty"
        controller.observe_actions(["give_empty_jar"])
        situation = pooh.get_scenario("eeyore_birthday").initial_situation.model_copy(
            update={"unresolved": [EMPTY_JAR_UNRESOLVED]},
        )

        # Whatever the participant's exact wording was (giving the jar as-is,
        # cleaning it up with a ribbon, or anything else), the machine-
        # readable action alone must be enough to clear this deterministically.
        updated = controller.synchronize_situation(situation)
        self.assertNotIn(EMPTY_JAR_UNRESOLVED, updated.unresolved)

    def test_synchronize_situation_clears_balloon_color_unresolved_without_example_support(self):
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: 0.0)
        controller.observe_settled_details(
            [SettledDetail(topic=BALLOON_COLOR_UNRESOLVED, value="青")]
        )
        self.assertEqual(controller.state.settled_details[BALLOON_COLOR_UNRESOLVED], "青")

        situation = pooh.get_scenario("eeyore_birthday").initial_situation.model_copy(
            update={"unresolved": [BALLOON_COLOR_UNRESOLVED]},
        )
        # Whatever wording settled it (Pooh's own guess or the participant's
        # own answer), the machine-readable action alone must be enough to
        # clear this deterministically.
        updated = controller.synchronize_situation(situation)
        self.assertNotIn(BALLOON_COLOR_UNRESOLVED, updated.unresolved)

    def test_synchronize_situation_clears_ribbon_color_unresolved_without_example_support(self):
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: 0.0)
        controller.observe_settled_details([SettledDetail(topic=RIBBON_COLOR_UNRESOLVED, value="赤")])
        self.assertEqual(controller.state.settled_details[RIBBON_COLOR_UNRESOLVED], "赤")

        situation = pooh.get_scenario("eeyore_birthday").initial_situation.model_copy(
            update={"unresolved": [RIBBON_COLOR_UNRESOLVED]},
        )
        updated = controller.synchronize_situation(situation)
        self.assertNotIn(RIBBON_COLOR_UNRESOLVED, updated.unresolved)

    def test_empty_jar_detail_is_ignored_before_honey_is_actually_empty(self):
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: 0.0)
        # Fired prematurely, before the honey is even gone: must not silently
        # pre-clear the unresolved item that does not exist yet.
        controller.observe_actions(["give_empty_jar"])
        self.assertNotIn(EMPTY_JAR_UNRESOLVED, controller.state.settled_details)

        now = [0.0]
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: now[0])
        controller.observe_actions(["commit_honey_jar_gift"])
        controller.observe_actions(["give_empty_jar"])
        now[0] = 30.0
        controller.pop_due_event()  # the tasting foreshadowing, not the eating itself
        now[0] = 40.0
        event = controller.pop_due_event()
        self.assertEqual(event.event_id, "pooh_ate_honey")
        situation = apply_situation_update(
            pooh.get_scenario("eeyore_birthday").initial_situation,
            event.situation_update,
        )
        updated = controller.synchronize_situation(situation)
        # The premature signal must not have hidden the real, later thread.
        self.assertIn(EMPTY_JAR_UNRESOLVED, updated.unresolved)

    def test_synchronize_situation_records_blocked_access_without_example_support(self):
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: 0.0)
        controller.observe_actions(["block_pooh_honey_access"])
        situation = pooh.get_scenario("eeyore_birthday").initial_situation

        updated = controller.synchronize_situation(situation)
        self.assertIn(BLOCKED_ACCESS_EVENT, updated.events)

    def test_synchronize_situation_clears_gift_unresolved_after_auto_fire_commit(self):
        now = [0.0]
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: now[0])
        now[0] = 30.0
        event = controller.pop_due_event()
        self.assertEqual(event.event_id, "honey_gift_committed")

        situation = pooh.get_scenario("eeyore_birthday").initial_situation
        updated = controller.synchronize_situation(situation)
        for item in GIFT_DECISION_UNRESOLVED:
            self.assertNotIn(item, updated.unresolved)

    def test_chat_does_not_reintroduce_resolved_gift_after_commit(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: 0.0)
        controller.observe_actions(["commit_honey_jar_gift"])

        agent = Mock(return_value=SimpleNamespace(
            interaction_mode="narrative",
            current_scene="3",
            narrative_actions=[],
            bot_response="困ったなあ。何かいいアイデアはあるかな？",
            updated_situation=scenario.initial_situation,
        ))
        answers = iter(["え，どうしよう，こまったね", "exit"])
        with patch("builtins.print"), patch.object(pooh, "append_log") as log:
            pooh.run_chat(
                agent,
                "test-model",
                "test-program",
                scenario,
                input_fn=lambda _prompt: next(answers),
                event_controller=controller,
            )

        turn = log.call_args_list[0].args[1]
        self.assertNotIn("ハチミツの入った壺を贈るのがいいと思うな", turn.bot_response)

    def test_chat_clears_gift_unresolved_on_the_same_turn_as_commit(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: 0.0)

        def agent(**kwargs):
            # The model's own situation_update leaves unresolved untouched;
            # the controller must still clear it on this same turn.
            return SimpleNamespace(
                interaction_mode="narrative",
                current_scene="1a",
                narrative_actions=["commit_honey_jar_gift"],
                bot_response="この蜂蜜の壺をあげるよ。",
                updated_situation=kwargs["current_situation"],
            )

        answers = iter(["プーは何をあげるの？", "exit"])
        with patch("builtins.print"), patch.object(pooh, "append_log") as log:
            pooh.run_chat(
                agent,
                "test-model",
                "test-program",
                scenario,
                input_fn=lambda _prompt: next(answers),
                event_controller=controller,
            )

        first_turn = log.call_args_list[0].args[1]
        for item in GIFT_DECISION_UNRESOLVED:
            self.assertNotIn(item, first_turn.updated_situation.unresolved)

    def test_chat_applies_narrative_actions_from_a_non_fixed_timed_event(self):
        scenario = pooh.get_scenario("eeyore_birthday")

        def fire_custom_event(state):
            state.completed_event_ids.add("custom_event")
            return WorldEvent(
                event_id="custom_event",
                description="テスト用の世界イベント。",
                scene_id="2",
                fallback_response="フォールバック応答。",
                situation_update=SituationUpdate(),
                fixed_response=False,
            )

        custom_event = RequiredNarrativeEvent(
            event_id="custom_event",
            delay_seconds=0.0,
            waits_for_quiet=False,
            prerequisite=lambda state: "custom_event" not in state.completed_event_ids,
            fire=fire_custom_event,
        )
        controller = HoneyGiftEventController(
            30.0, 30.0, 10.0, clock=lambda: 0.0, required_events=(custom_event,),
        )

        def agent(**kwargs):
            return SimpleNamespace(
                interaction_mode="narrative",
                current_scene="2",
                narrative_actions=["block_pooh_honey_access"],
                bot_response="テスト応答。",
                updated_situation=kwargs["current_situation"],
            )

        with patch("builtins.print"), patch.object(pooh, "append_log") as log:
            pooh.run_chat(
                agent,
                "test-model",
                "test-program",
                scenario,
                input_fn=lambda _prompt: "exit",
                event_controller=controller,
            )

        self.assertEqual(log.call_count, 1)
        event_turn = log.call_args_list[0].args[1]
        self.assertEqual(event_turn.narrative_actions, ["block_pooh_honey_access"])
        self.assertEqual(controller.state.access_restriction, "blocked")
        self.assertIn(BLOCKED_ACCESS_EVENT, event_turn.updated_situation.events)

    def test_chat_delivers_timed_event_without_participant_input(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        now = [0.0]
        controller = HoneyGiftEventController(30.0, 30.0, 10.0, clock=lambda: now[0])

        def agent(**kwargs):
            if kwargs["world_event"]:
                # Both timed events fire with fixed_response=True, so this
                # branch is never actually invoked; kept for documentation.
                return SimpleNamespace(
                    interaction_mode="narrative",
                    current_scene="3",
                    narrative_actions=[],
                    bot_response="あっ、みんな食べちゃった。",
                    updated_situation=kwargs["current_situation"],
                )
            return SimpleNamespace(
                interaction_mode="narrative",
                current_scene="1a",
                narrative_actions=["commit_honey_jar_gift"],
                bot_response="この蜂蜜の壺をあげるよ。",
                updated_situation=kwargs["current_situation"],
            )

        input_count = [0]
        def input_fn(_prompt):
            input_count[0] += 1
            if input_count[0] == 1:
                return "プーは何をあげるの？"
            if input_count[0] == 2:
                now[0] = 31.0  # past the tasting foreshadowing's deadline (30s)
                return ""
            if input_count[0] == 3:
                now[0] = 42.0  # past the eating deadline (31 + 10s)
                return ""
            return "exit"

        with patch("builtins.print"), patch.object(pooh, "append_log") as log:
            pooh.run_chat(
                agent,
                "test-model",
                "test-program",
                scenario,
                input_fn=input_fn,
                event_controller=controller,
            )

        self.assertEqual(log.call_count, 3)
        taste_turn = log.call_args_list[1].args[1]
        self.assertEqual(taste_turn.source, "world_event")
        self.assertEqual(taste_turn.scene_id, "2")
        # At the time the tasting turn was logged, the honey was not yet gone.
        self.assertIn("蜂蜜壺", taste_turn.updated_situation.props)
        self.assertNotIn("空になった蜂蜜壺", taste_turn.updated_situation.props)

        event_turn = log.call_args_list[2].args[1]
        self.assertEqual(event_turn.source, "world_event")
        self.assertEqual(event_turn.scene_id, "3")
        self.assertIn("空になった蜂蜜壺", event_turn.updated_situation.props)
        self.assertEqual(controller.state.honey_status, "empty")

    def test_fixed_timed_event_skips_generation_and_keeps_original_line(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        controller = HoneyGiftEventController(0.0, 0.0, 0.0, clock=lambda: 1.0)
        controller.observe_actions(["commit_honey_jar_gift"])
        controller.pop_due_event()  # tasting foreshadowing fires first (delay is 0)
        event = controller.pop_due_event()
        situation = apply_situation_update(
            scenario.initial_situation,
            event.situation_update,
        )

        agent = Mock(side_effect=RuntimeError("generation should be skipped"))
        result, latency_ms, used_fallback = pooh._event_result(
            agent,
            situation,
            event,
            "",
        )

        self.assertFalse(used_fallback)
        agent.assert_not_called()
        self.assertEqual(latency_ms, 0.0)
        self.assertEqual(result.bot_response, HONEY_EATEN_RESPONSE)
        self.assertIn("空になった蜂蜜壺", result.updated_situation.props)

    def test_score_is_bounded(self):
        self.assertEqual(pooh.clamp_score(9), 5)
        self.assertEqual(pooh.clamp_score("0"), 1)
        self.assertEqual(pooh.clamp_score("bad"), 1)


if __name__ == "__main__":
    unittest.main()
