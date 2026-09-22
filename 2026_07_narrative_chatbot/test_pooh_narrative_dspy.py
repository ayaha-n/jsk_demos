"""LLM/APIを呼ばずに行う最小回帰テスト。"""

import ast
import sys
import types
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch
from types import SimpleNamespace
from pathlib import Path


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
    GIFT_CANCELLED_EVENT,
    GIFT_DECISION_UNRESOLVED,
    GIFT_DELIVERED_EVENT,
    HONEY_EATEN_RESPONSE,
    HONEY_GIFT_COMMITTED_EVENT,
    HONEY_PREPARATION_UNRESOLVED,
    HONEY_TASTED_EVENT,
    JAR_WITH_PARTICIPANT_EVENT,
    HoneyGiftEventController,
    RequiredNarrativeEvent,
    WorldEvent,
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

    def test_history_retains_mode_and_updated_state(self):
        turn = pooh.Turn("行為", "ordinary", "応答", "状態")
        history = pooh.format_history([turn])
        for value in ("行為", "ordinary", "応答", "状態"):
            self.assertIn(value, history)

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
            event_inactivity_delay_seconds=scenario.event_inactivity_delay_seconds + 10,
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
        controller = HoneyGiftEventController(30.0, clock=lambda: now[0])
        controller.observe_actions(["commit_honey_jar_gift"])
        # The commit-to-eating span (30s) is split in half around the
        # tasting foreshadowing, so the first pending stage is due in 15s.
        self.assertEqual(controller.seconds_until_due(), 15.0)

        now[0] = 110.0
        controller.observe_actions(["commit_honey_jar_gift"])
        self.assertEqual(controller.seconds_until_due(), 5.0)
        self.assertIsNone(controller.pop_due_event())

        now[0] = 115.0
        taste_event = controller.pop_due_event()
        self.assertIsNotNone(taste_event)
        self.assertEqual(taste_event.event_id, "pooh_tastes_honey")
        self.assertEqual(controller.state.honey_status, "full")
        self.assertEqual(controller.seconds_until_due(), 15.0)

        now[0] = 130.0
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
        controller = HoneyGiftEventController(30.0, clock=lambda: now[0])
        now[0] = 30.0
        commit_event = controller.pop_due_event()
        self.assertIsNotNone(commit_event)
        self.assertEqual(commit_event.event_id, "honey_gift_committed")
        self.assertEqual(commit_event.scene_id, "1c")
        self.assertEqual(controller.state.gift_status, "committed")

        now[0] = 44.9
        self.assertIsNone(controller.pop_due_event())
        now[0] = 45.0
        taste_event = controller.pop_due_event()
        self.assertIsNotNone(taste_event)
        self.assertEqual(taste_event.event_id, "pooh_tastes_honey")
        self.assertEqual(taste_event.scene_id, "2")

        now[0] = 59.9
        self.assertIsNone(controller.pop_due_event())
        now[0] = 60.0
        eat_event = controller.pop_due_event()
        self.assertIsNotNone(eat_event)
        self.assertEqual(eat_event.event_id, "pooh_ate_honey")
        self.assertEqual(eat_event.scene_id, "3")

    def test_participant_input_does_not_reset_eating_timer(self):
        now = [0.0]
        controller = HoneyGiftEventController(30.0, clock=lambda: now[0])
        controller.observe_actions(["commit_honey_jar_gift"])
        seen_event_ids = []
        for _ in range(5):
            now[0] += 20.0
            controller.observe_user_input()
            event = controller.pop_due_event()
            if event is not None:
                seen_event_ids.append(event.event_id)
                if event.event_id == "pooh_ate_honey":
                    return
        self.assertIn(
            "pooh_ate_honey",
            seen_event_ids,
            "eating event was indefinitely postponed by unrelated input",
        )

    def test_honey_event_is_cancelled_when_jar_leaves_pooh(self):
        now = [0.0]
        controller = HoneyGiftEventController(30.0, clock=lambda: now[0])
        controller.observe_actions(["commit_honey_jar_gift"])
        controller.observe_actions(["give_honey_jar_to_participant"])
        now[0] = 60.0
        self.assertIsNone(controller.pop_due_event())
        self.assertEqual(controller.state.jar_holder, "participant")
        self.assertEqual(controller.state.honey_status, "full")

    def test_synchronize_situation_clears_gift_unresolved_without_example_support(self):
        now = [0.0]
        controller = HoneyGiftEventController(30.0, clock=lambda: now[0])
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

    def test_synchronize_situation_does_not_claim_a_decision_that_never_happened(self):
        now = [0.0]
        controller = HoneyGiftEventController(30.0, clock=lambda: now[0])
        # Cancelling without ever committing must not fabricate a decision.
        controller.observe_actions(["cancel_honey_jar_gift"])
        situation = pooh.get_scenario("eeyore_birthday").initial_situation
        updated = controller.synchronize_situation(situation)
        self.assertNotIn(HONEY_GIFT_COMMITTED_EVENT, updated.events)
        for item in GIFT_DECISION_UNRESOLVED:
            self.assertIn(item, updated.unresolved)

    def test_synchronize_situation_clears_empty_jar_unresolved_without_example_support(self):
        controller = HoneyGiftEventController(30.0, clock=lambda: 0.0)
        controller.observe_actions(["commit_honey_jar_gift"])
        controller.state.honey_status = "empty"
        controller.observe_actions(["resolve_empty_jar_gift"])
        situation = pooh.get_scenario("eeyore_birthday").initial_situation.model_copy(
            update={"unresolved": [EMPTY_JAR_UNRESOLVED]},
        )

        # Whatever the participant's exact wording was (giving the jar as-is,
        # cleaning it up with a ribbon, or anything else), the machine-
        # readable action alone must be enough to clear this deterministically.
        updated = controller.synchronize_situation(situation)
        self.assertNotIn(EMPTY_JAR_UNRESOLVED, updated.unresolved)

    def test_synchronize_situation_clears_balloon_color_unresolved_without_example_support(self):
        controller = HoneyGiftEventController(30.0, clock=lambda: 0.0)
        controller.observe_actions(["resolve_balloon_color"])
        self.assertIn("balloon_color_resolved", controller.state.completed_event_ids)

        situation = pooh.get_scenario("eeyore_birthday").initial_situation.model_copy(
            update={"unresolved": [BALLOON_COLOR_UNRESOLVED]},
        )
        # Whatever wording settled it (Pooh's own guess or the participant's
        # own answer), the machine-readable action alone must be enough to
        # clear this deterministically.
        updated = controller.synchronize_situation(situation)
        self.assertNotIn(BALLOON_COLOR_UNRESOLVED, updated.unresolved)

    def test_resolve_empty_jar_gift_is_ignored_before_honey_is_actually_empty(self):
        controller = HoneyGiftEventController(30.0, clock=lambda: 0.0)
        # Fired prematurely, before the honey is even gone: must not silently
        # pre-clear the unresolved item that does not exist yet.
        controller.observe_actions(["resolve_empty_jar_gift"])
        self.assertNotIn("empty_jar_gift_resolved", controller.state.completed_event_ids)

        now = [0.0]
        controller = HoneyGiftEventController(30.0, clock=lambda: now[0])
        controller.observe_actions(["commit_honey_jar_gift"])
        controller.observe_actions(["resolve_empty_jar_gift"])
        now[0] = 15.0
        controller.pop_due_event()  # the tasting foreshadowing, not the eating itself
        now[0] = 30.0
        event = controller.pop_due_event()
        self.assertEqual(event.event_id, "pooh_ate_honey")
        situation = apply_situation_update(
            pooh.get_scenario("eeyore_birthday").initial_situation,
            event.situation_update,
        )
        updated = controller.synchronize_situation(situation)
        # The premature signal must not have hidden the real, later thread.
        self.assertIn(EMPTY_JAR_UNRESOLVED, updated.unresolved)

    def test_synchronize_situation_records_jar_with_participant_without_example_support(self):
        controller = HoneyGiftEventController(30.0, clock=lambda: 0.0)
        controller.observe_actions(["give_honey_jar_to_participant"])
        situation = pooh.get_scenario("eeyore_birthday").initial_situation

        updated = controller.synchronize_situation(situation)
        self.assertIn(JAR_WITH_PARTICIPANT_EVENT, updated.events)

    def test_synchronize_situation_records_blocked_access_without_example_support(self):
        controller = HoneyGiftEventController(30.0, clock=lambda: 0.0)
        controller.observe_actions(["block_pooh_honey_access"])
        situation = pooh.get_scenario("eeyore_birthday").initial_situation

        updated = controller.synchronize_situation(situation)
        self.assertIn(BLOCKED_ACCESS_EVENT, updated.events)

    def test_synchronize_situation_reopens_gift_unresolved_after_cancel(self):
        controller = HoneyGiftEventController(30.0, clock=lambda: 0.0)
        controller.observe_actions(["commit_honey_jar_gift"])
        controller.observe_actions(["cancel_honey_jar_gift"])
        situation = pooh.get_scenario("eeyore_birthday").initial_situation.model_copy(
            update={"unresolved": [], "events": [HONEY_GIFT_COMMITTED_EVENT]},
        )

        updated = controller.synchronize_situation(situation)
        # The earlier decision stays on record, but cancelling it reopens
        # what to give (and that prep isn't done) rather than leaving both
        # silently marked as resolved.
        self.assertIn(HONEY_GIFT_COMMITTED_EVENT, updated.events)
        self.assertIn(GIFT_CANCELLED_EVENT, updated.events)
        for item in GIFT_DECISION_UNRESOLVED:
            self.assertIn(item, updated.unresolved)
        self.assertNotIn(HONEY_PREPARATION_UNRESOLVED, updated.unresolved)

    def test_synchronize_situation_records_delivery_and_clears_preparation_unresolved(self):
        controller = HoneyGiftEventController(30.0, clock=lambda: 0.0)
        controller.observe_actions(["commit_honey_jar_gift"])
        controller.observe_actions(["deliver_honey_jar_to_eeyore"])
        situation = pooh.get_scenario("eeyore_birthday").initial_situation.model_copy(
            update={"unresolved": [HONEY_PREPARATION_UNRESOLVED]},
        )

        updated = controller.synchronize_situation(situation)
        self.assertIn(GIFT_DELIVERED_EVENT, updated.events)
        self.assertNotIn(HONEY_PREPARATION_UNRESOLVED, updated.unresolved)

    def test_synchronize_situation_clears_gift_unresolved_after_auto_fire_commit(self):
        now = [0.0]
        controller = HoneyGiftEventController(30.0, clock=lambda: now[0])
        now[0] = 30.0
        event = controller.pop_due_event()
        self.assertEqual(event.event_id, "honey_gift_committed")

        situation = pooh.get_scenario("eeyore_birthday").initial_situation
        updated = controller.synchronize_situation(situation)
        for item in GIFT_DECISION_UNRESOLVED:
            self.assertNotIn(item, updated.unresolved)

    def test_chat_does_not_reintroduce_resolved_gift_after_commit(self):
        scenario = pooh.get_scenario("eeyore_birthday")
        controller = HoneyGiftEventController(30.0, clock=lambda: 0.0)
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
        controller = HoneyGiftEventController(30.0, clock=lambda: 0.0)

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
            resets_on_input=False,
            prerequisite=lambda state: "custom_event" not in state.completed_event_ids,
            fire=fire_custom_event,
        )
        controller = HoneyGiftEventController(
            30.0, clock=lambda: 0.0, required_events=(custom_event,),
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
        controller = HoneyGiftEventController(30.0, clock=lambda: now[0])

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
                now[0] = 16.0  # past the tasting foreshadowing's deadline (15s)
                return ""
            if input_count[0] == 3:
                now[0] = 32.0  # past the eating deadline (16 + 15s)
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
        controller = HoneyGiftEventController(0.0, clock=lambda: 1.0)
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
